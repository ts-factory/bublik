# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Remote MCP servers a user registers for their own chat runs.

Header values are stored encrypted and never returned, only their names.
"""

from __future__ import annotations

import re
from typing import ClassVar

from django.db import models
from django.utils.text import slugify

from bublik.core.crypto import decrypt_json, encrypt_json
from bublik.data.models.user import User


__all__ = [
    'SLUG_MAX_LEN',
    'UserMcpServer',
    'slug_for',
]


# The slug prefixes tool names; kept short for provider tool-name limits.
SLUG_MAX_LEN = 32

_SLUG_SEPARATORS = re.compile(r'[^a-z0-9]+')


def slug_for(name: str) -> str:
    """Derive the tool-name prefix from a display name: ``[a-z0-9_]`` only."""
    slug = _SLUG_SEPARATORS.sub('_', slugify(name).lower())
    return slug.strip('_')[:SLUG_MAX_LEN].strip('_')


class UserMcpServer(models.Model):
    """A Streamable-HTTP MCP server owned by, and attached only to, one user."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='mcp_servers',
        help_text='The only user whose chat runs see this server.',
    )
    name = models.CharField(
        max_length=64,
        help_text='Human-readable name shown in settings.',
    )
    slug = models.CharField(
        max_length=SLUG_MAX_LEN,
        db_index=True,
        help_text='Derived from the name; prefixes every tool name the server exposes.',
    )
    url = models.CharField(
        max_length=2048,
        help_text='Streamable HTTP endpoint of the server.',
    )
    enabled = models.BooleanField(
        default=True,
        help_text='Disabled servers are kept but not attached to chat runs.',
    )
    headers_encrypted = models.TextField(
        blank=True,
        default='',
        help_text='Fernet token over the JSON header mapping. Empty means no headers.',
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'bublik_user_mcp_server'
        ordering: ClassVar[list] = ['name', 'id']
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=['user', 'slug'],
                name='uniq_mcp_server_slug_per_user',
            ),
        ]

    def __repr__(self):
        # Never include headers.
        return (
            f'UserMcpServer(id={self.pk!r}, user={self.user_id!r}, name={self.name!r}, '
            f'slug={self.slug!r}, url={self.url!r}, enabled={self.enabled!r})'
        )

    def get_headers(self) -> dict[str, str]:
        """Decrypt the header mapping. Raises :class:`~bublik.core.crypto.DecryptionError`."""
        if not self.headers_encrypted:
            return {}
        return decrypt_json(self.headers_encrypted)

    def set_headers(self, headers: dict[str, str]) -> None:
        """Replace the header mapping; an empty mapping stores nothing at all."""
        self.headers_encrypted = encrypt_json(headers) if headers else ''

    @property
    def header_names(self) -> list[str]:
        return sorted(self.get_headers())
