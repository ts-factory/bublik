# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Remote MCP servers for the chat agent.

Two sources, both over Streamable HTTP:

* the admin ``mcp_servers`` list of the ``ai`` config, whose header values may
  reference ``${env:AI_NAME}`` / ``${settings:AI_NAME}``, resolved at agent-build
  time;
* the servers users register themselves, with literal encrypted header values,
  attached per run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx
from pydantic_ai.mcp import MCPToolset

from bublik.ai.config import resolve_headers
from bublik.core.crypto import DecryptionError
from bublik.core.user_mcp_server.services import public_address


if TYPE_CHECKING:
    from collections.abc import Collection, Iterable

    from pydantic_ai.toolsets import AbstractToolset

    from bublik.ai.types import AiConfig, UserMcpServersConfig
    from bublik.data.models import UserMcpServer


logger = logging.getLogger(__name__)

# Connect timeout for admin-configured servers (pydantic-ai's default).
ADMIN_INIT_TIMEOUT_S = 5.0
# The same timeouts the MCP SDK uses for its own HTTP client.
_USER_HTTP_TIMEOUT = httpx.Timeout(30.0, read=300.0)


def user_tool_prefix(slug: str) -> str:
    """Tool-name prefix of a user's server.

    ``prefixed()`` adds one more ``_``, giving ``{slug}__{tool}``. Slugs never
    contain ``__``, so these names cannot clash with built-in tools or with
    another user server's tools.
    """
    return f'{slug}_'


class _PolicyTransport(httpx.AsyncHTTPTransport):
    """Applies the user-server host policy to every request.

    A host that is not listed is connected to by the public address that was
    just checked, so neither a redirect nor a DNS change can reach a private
    one. TLS still verifies the original host name.
    """

    def __init__(self, policy: UserMcpServersConfig):
        super().__init__()
        self._policy = policy

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if not self._policy.host_listed(host):
            address = None
            if self._policy.allow_any_public_host:
                address = await asyncio.to_thread(public_address, host)
            if address is None:
                msg = f'Host {host!r} is not allowed by the MCP server policy'
                raise httpx.ConnectError(msg, request=request)
            request.url = request.url.copy_with(host=address)
            request.extensions = {**request.extensions, 'sni_hostname': host}
        return await super().handle_async_request(request)


def _make_toolset(
    url: str,
    headers: dict[str, str],
    prefix: str,
    *,
    init_timeout: float,
    policy: UserMcpServersConfig | None = None,
) -> AbstractToolset:
    """One prefixed Streamable-HTTP toolset; ``policy`` guards a user's server."""
    if policy is None:
        toolset = MCPToolset(url, headers=headers or None, init_timeout=init_timeout)
    else:
        client = httpx.AsyncClient(
            headers=headers,
            transport=_PolicyTransport(policy),
            follow_redirects=False,
            timeout=_USER_HTTP_TIMEOUT,
        )
        toolset = MCPToolset(url, http_client=client, init_timeout=init_timeout)
    return toolset.prefixed(prefix)


def build_mcp_toolsets(config: AiConfig) -> list[AbstractToolset]:
    """Build a Streamable-HTTP toolset for each usable admin-configured server.

    Each server's ``id`` becomes the tool-name prefix so its tools cannot
    collide with the built-in Bublik tools. Servers with unresolved header
    references and servers that fail to construct are logged and skipped: a
    single bad remote must not break the whole agent. The connections are
    opened per run by the caller (``async with agent:``), not here.
    """
    toolsets = []
    for server in config.mcp_servers:
        headers = resolve_headers(server.headers, f'MCP server {server.id!r}')
        if headers is None:
            continue
        try:
            toolsets.append(
                _make_toolset(
                    server.url, headers, server.id, init_timeout=ADMIN_INIT_TIMEOUT_S
                ),
            )
        except Exception:
            logger.exception('failed to build MCP toolset for server %r', server.id)
    return toolsets


def build_user_mcp_toolsets(
    servers: Iterable[UserMcpServer],
    reserved_ids: Collection[str],
    policy: UserMcpServersConfig,
) -> list[AbstractToolset]:
    """Build a toolset for each of a user's servers that can be attached.

    Tool names are prefixed by :func:`user_tool_prefix`. Servers whose slug is
    an admin server id in ``reserved_ids``, or whose headers do not decrypt,
    are skipped. Header values are literal, never resolved as ``${env:...}``,
    and every request is checked against ``policy``.
    """
    toolsets = []
    for server in servers:
        label = f'user MCP server {server.pk!r} ({server.slug!r})'
        if server.slug in reserved_ids:
            logger.warning('%s skipped: prefix collides with an admin-configured server', label)
            continue
        try:
            headers = server.get_headers()
        except DecryptionError:
            logger.warning('%s skipped: headers cannot be decrypted', label)
            continue
        # Also refused at save time.
        if any(
            '\r' in value or '\n' in value or not value.isascii() for value in headers.values()
        ):
            logger.warning('%s skipped: a header value is not a valid header value', label)
            continue
        try:
            toolsets.append(
                _make_toolset(
                    server.url,
                    headers,
                    user_tool_prefix(server.slug),
                    init_timeout=policy.connect_timeout_s,
                    policy=policy,
                ),
            )
        except Exception:
            logger.exception('failed to build toolset for %s', label)
    return toolsets
