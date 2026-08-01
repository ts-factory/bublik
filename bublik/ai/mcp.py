# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Remote MCP servers for the chat agent.

Servers are declared in the ``ai`` config's ``mcp_servers`` list. Header
values may embed source-qualified ``${env:AI_NAME}`` and
``${settings:AI_NAME}`` references at agent-build time (with the same strict
``AI_``-prefix guard as provider API keys); like key rotation, rotating a token
without editing the config does not bust the agent cache (its fingerprint is a
digest of the authored config).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from pydantic_ai.mcp import MCPToolset

from bublik.ai.config import resolve_secret_reference


if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset

    from bublik.ai.types import AiConfig, McpServer


logger = logging.getLogger(__name__)

# Match every complete ${...} placeholder. The shared strict resolver decides
# whether its source and AI_-prefixed name are valid; static text passes through.
_SECRET_PLACEHOLDER_RE = re.compile(r'\$\{[^}]*\}')


def resolve_mcp_headers(server: McpServer) -> dict[str, str] | None:
    """Resolve all source-qualified secret references in a server's headers.

    Any complete placeholder with invalid syntax, a non-``AI_`` name, or an
    unresolved value makes the whole server unusable. The caller then skips it
    rather than sending a literal placeholder in an outbound header.
    """
    resolved: dict[str, str] = {}
    for key, value in server.headers.items():
        unresolved: list[str] = []

        def _sub(match: re.Match, _unresolved=unresolved) -> str:
            reference = match.group(0)
            value = resolve_secret_reference(reference)
            if value is None:
                _unresolved.append(reference)
                return ''
            return value

        substituted = _SECRET_PLACEHOLDER_RE.sub(_sub, value)
        if unresolved:
            logger.warning(
                'MCP server %r skipped: unresolved reference(s) %s in header %r',
                server.id,
                ', '.join(unresolved),
                key,
            )
            return None
        resolved[key] = substituted
    return resolved


def build_mcp_toolsets(config: AiConfig) -> list[AbstractToolset]:
    """Build a Streamable-HTTP toolset for each usable remote MCP server.

    Each server's ``id`` becomes the tool-name prefix so its tools cannot
    collide with the built-in Bublik tools. Servers with unresolved header
    references and servers that fail to construct are logged and skipped: a
    single bad remote must not break the whole agent. The connections are
    opened per run by the caller (``async with agent:``), not here.
    """
    toolsets = []
    for server in config.mcp_servers:
        headers = resolve_mcp_headers(server)
        if headers is None:
            continue
        try:
            toolsets.append(
                MCPToolset(server.url, headers=headers or None).prefixed(server.id),
            )
        except Exception:
            logger.exception('failed to build MCP toolset for server %r', server.id)
    return toolsets
