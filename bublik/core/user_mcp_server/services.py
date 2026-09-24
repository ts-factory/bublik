# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Registering, updating and listing users' own MCP servers.

The host policy comes from ``user_mcp_servers`` in the ``ai`` config and allows
nothing until an admin sets it.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import TYPE_CHECKING, NoReturn
from urllib.parse import urlsplit

from django.db import IntegrityError, transaction
from rest_framework.exceptions import ValidationError

from bublik.ai.config import get_ai_config
from bublik.core.crypto import DecryptionError
from bublik.core.exceptions import NotFoundError
from bublik.core.user_mcp_server.dto import UserMcpServerDTO
from bublik.data.models import User, UserMcpServer, slug_for


if TYPE_CHECKING:
    from bublik.ai.types import UserMcpServersConfig


logger = logging.getLogger(__name__)

MAX_NAME_LEN = 64
MAX_URL_LEN = 2048
MAX_HEADERS = 32
MAX_HEADER_NAME_LEN = 128
MAX_HEADER_VALUE_LEN = 4096

# RFC 7230 ``token``: the characters a header field name may consist of.
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
# Headers the HTTP client manages itself.
_RESERVED_HEADERS = frozenset({'host', 'content-length', 'transfer-encoding', 'connection'})

_UNSET = object()


# NAT64 well-known prefix: the embedded IPv4 address is what gets reached.
_NAT64 = ipaddress.ip_network('64:ff9b::/96')


def _address_is_private(address: str) -> bool:
    """Whether ``address`` is anything but a public unicast address."""
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return not ip.is_global or ip.is_multicast


def _resolve(host: str) -> set[str]:
    try:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError:
        return set()


def _local_name(host: str) -> bool:
    return host == 'localhost' or host.endswith('.localhost')


def classify_host(host: str) -> str:
    """Return ``'private'``, ``'public'`` or ``'unresolved'`` for a URL host.

    A name is private if any of its addresses is.
    """
    host = host.lower().rstrip('.')
    if _local_name(host):
        return 'private'
    try:
        return 'private' if _address_is_private(host) else 'public'
    except ValueError:
        pass
    addresses = _resolve(host)
    if not addresses:
        return 'unresolved'
    return 'private' if any(_address_is_private(a) for a in addresses) else 'public'


def public_address(host: str) -> str | None:
    """A public address of ``host`` to connect to, or ``None`` if any of them is private."""
    host = host.lower().rstrip('.')
    if _local_name(host):
        return None
    try:
        return None if _address_is_private(host) else host
    except ValueError:
        pass
    addresses = _resolve(host)
    if not addresses or any(_address_is_private(a) for a in addresses):
        return None
    return sorted(addresses)[0]


def host_refusal(policy: UserMcpServersConfig, host: str) -> str | None:
    """Why the policy refuses ``host``, or ``None`` when it allows it.

    Listing a host is the only way to allow a private one.
    """
    if policy.host_listed(host):
        return None
    remedy = (
        'Ask an administrator to add it to `user_mcp_servers.allowed_hosts` in the AI config.'
    )
    if not policy.allow_any_public_host:
        return f'Host {host!r} is not allowed. {remedy}'
    kind = classify_host(host)
    if kind == 'public':
        return None
    if kind == 'unresolved':
        return (
            f'Host {host!r} could not be resolved. Check the address; '
            f'if it is only reachable from Bublik, {remedy[0].lower()}{remedy[1:]}'
        )
    return (
        f'Host {host!r} is a private or loopback address, which the policy does not '
        f'allow. {remedy}'
    )


def host_allowed(policy: UserMcpServersConfig, host: str) -> bool:
    return host_refusal(policy, host) is None


def _reject(field: str, message: str) -> NoReturn:
    raise ValidationError({field: [message]})


def validate_name(name: str) -> tuple[str, str]:
    """Normalise a display name and derive its slug. Returns ``(name, slug)``."""
    name = name.strip()
    if not name:
        _reject('name', 'A server needs a name')
    if len(name) > MAX_NAME_LEN:
        _reject('name', f'Name must be at most {MAX_NAME_LEN} characters')
    slug = slug_for(name)
    if not slug:
        _reject('name', 'Name must contain at least one letter or digit')
    return name, slug


def validate_url(url: str, policy: UserMcpServersConfig) -> str:
    """Check the URL shape and that its host passes the admin policy."""
    url = url.strip()
    if not url:
        _reject('url', 'A server needs a URL')
    if len(url) > MAX_URL_LEN:
        _reject('url', f'URL must be at most {MAX_URL_LEN} characters')
    if any(c.isspace() for c in url):
        _reject('url', 'URL must not contain whitespace')
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        _reject('url', 'URL is malformed')
    if parts.scheme not in ('http', 'https'):
        _reject('url', 'URL must start with http:// or https://')
    if not host:
        _reject('url', 'URL must include a host')
    if parts.username is not None or parts.password is not None:
        _reject('url', 'Put credentials in a header, not in the URL')
    refusal = host_refusal(policy, host)
    if refusal:
        _reject('url', refusal)
    return url


def validate_headers(headers: dict[str, str]) -> dict[str, str]:
    """Check a complete header mapping (names and literal values)."""
    if len(headers) > MAX_HEADERS:
        _reject('headers', f'At most {MAX_HEADERS} headers are allowed')
    seen: set[str] = set()
    for name, value in headers.items():
        if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
            _reject('headers', f'Invalid header name {name!r}')
        if len(name) > MAX_HEADER_NAME_LEN:
            _reject('headers', f'Header name {name!r} is too long')
        lowered = name.lower()
        if lowered in _RESERVED_HEADERS:
            _reject('headers', f'Header {name!r} cannot be set')
        if lowered in seen:
            _reject('headers', f'Header {name!r} is given more than once')
        seen.add(lowered)
        if not isinstance(value, str) or not value:
            _reject('headers', f'Header {name!r} needs a value')
        if len(value) > MAX_HEADER_VALUE_LEN:
            _reject('headers', f'Value of header {name!r} is too long')
        # A line break would inject extra headers.
        if '\r' in value or '\n' in value:
            _reject('headers', f'Value of header {name!r} must not contain line breaks')
        if not value.isascii():
            _reject('headers', f'Value of header {name!r} must contain only ASCII characters')
    return dict(headers)


def merge_headers(current: dict[str, str], patch: dict[str, str | None]) -> dict[str, str]:
    """Apply a PATCH mapping: a value sets the header, ``None`` removes it.

    Names match case-insensitively.
    """
    merged = dict(current)
    for name, value in patch.items():
        for existing in [k for k in merged if k.lower() == name.lower()]:
            del merged[existing]
        if value is not None:
            merged[name] = value
    return merged


def _save(server: UserMcpServer, **kwargs) -> None:
    try:
        with transaction.atomic():
            server.save(**kwargs)
    except IntegrityError as e:
        raise ValidationError({'name': ['You already have a server with this name']}) from e


class UserMcpServerService:
    """Users' own MCP servers."""

    @staticmethod
    def policy() -> UserMcpServersConfig:
        return get_ai_config().user_mcp_servers

    @staticmethod
    def to_dto(server: UserMcpServer) -> UserMcpServerDTO:
        try:
            header_names = server.header_names
        except DecryptionError:
            # Kept visible so the user can fix or delete it.
            logger.warning('user MCP server %r: headers cannot be decrypted', server.pk)
            header_names = []
        return UserMcpServerDTO(
            id=server.id,
            name=server.name,
            slug=server.slug,
            url=server.url,
            enabled=server.enabled,
            header_names=header_names,
            created=server.created,
            updated=server.updated,
        )

    @staticmethod
    def list_for_user(user: User) -> list[UserMcpServerDTO]:
        servers = UserMcpServer.objects.filter(user=user)
        return [UserMcpServerService.to_dto(server) for server in servers]

    @staticmethod
    def enabled_for(user_id: int) -> list[UserMcpServer]:
        """The model rows to attach to a chat run; headers stay encrypted here."""
        return list(UserMcpServer.objects.filter(user_id=user_id, enabled=True))

    @staticmethod
    def all_for(user_id: int) -> list[UserMcpServer]:
        """Every server of the user, disabled ones included, for the status report."""
        return list(UserMcpServer.objects.filter(user_id=user_id))

    @staticmethod
    def _get_owned(user: User, server_id) -> UserMcpServer:
        try:
            server = UserMcpServer.objects.get(pk=server_id)
        except (UserMcpServer.DoesNotExist, TypeError, ValueError) as e:
            msg = f'MCP server {server_id} not found'
            raise NotFoundError(msg) from e
        # Report someone else's server as missing to not reveal that it exists.
        if server.user_id != user.id:
            msg = f'MCP server {server_id} not found'
            raise NotFoundError(msg)
        return server

    @staticmethod
    def _check_slug_free(user: User, slug: str, exclude_pk=None) -> None:
        if slug in get_ai_config().mcp_server_ids:
            _reject(
                'name',
                f'The name {slug!r} is taken by a server configured by an administrator',
            )
        others = UserMcpServer.objects.filter(user=user, slug=slug)
        if exclude_pk is not None:
            others = others.exclude(pk=exclude_pk)
        if others.exists():
            _reject('name', 'You already have a server with this name')

    @staticmethod
    def create(
        user: User,
        name: str,
        url: str,
        headers: dict[str, str] | None = None,
        enabled: bool = True,
    ) -> UserMcpServerDTO:
        """Register a server. Raises ``ValidationError`` on any rule breach."""
        policy = UserMcpServerService.policy()
        name, slug = validate_name(name)
        url = validate_url(url, policy)
        headers = validate_headers(headers or {})

        # Lock the user so concurrent requests cannot exceed the limit.
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            if UserMcpServer.objects.filter(user=user).count() >= policy.max_per_user:
                msg = (
                    f'You have reached the limit of {policy.max_per_user} MCP servers. '
                    'Delete one before adding another.'
                )
                raise ValidationError(msg)
            UserMcpServerService._check_slug_free(user, slug)
            server = UserMcpServer(user=user, name=name, slug=slug, url=url, enabled=enabled)
            server.set_headers(headers)
            _save(server)
        return UserMcpServerService.to_dto(server)

    @staticmethod
    def update(
        user: User,
        server_id,
        *,
        name: str | object = _UNSET,
        url: str | object = _UNSET,
        enabled: bool | object = _UNSET,
        headers: dict[str, str | None] | None = None,
    ) -> UserMcpServerDTO:
        """Change any subset of fields; ``headers`` is merged, see :func:`merge_headers`."""
        with transaction.atomic():
            # Lock the user so concurrent renames cannot take the same name.
            User.objects.select_for_update().get(pk=user.pk)
            server = UserMcpServerService._get_owned(user, server_id)
            update_fields = ['updated']
            if name is not _UNSET:
                server.name, slug = validate_name(name)
                if slug != server.slug:
                    UserMcpServerService._check_slug_free(user, slug, exclude_pk=server.pk)
                    server.slug = slug
                update_fields += ['name', 'slug']
            if url is not _UNSET and url.strip() != server.url:
                server.url = validate_url(url, UserMcpServerService.policy())
                update_fields.append('url')
            if enabled is not _UNSET:
                server.enabled = bool(enabled)
                update_fields.append('enabled')
            if headers:
                try:
                    current = server.get_headers()
                except DecryptionError:
                    # Undecryptable headers are unusable anyway; start afresh.
                    current = {}
                server.set_headers(validate_headers(merge_headers(current, headers)))
                update_fields.append('headers_encrypted')
            _save(server, update_fields=update_fields)
        return UserMcpServerService.to_dto(server)

    @staticmethod
    def delete(user: User, server_id) -> None:
        UserMcpServerService._get_owned(user, server_id).delete()
