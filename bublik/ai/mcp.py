# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Remote MCP servers for the chat agent.

Servers are declared in the ``ai`` config's ``mcp_servers`` list. Header
values may embed source-qualified ``${env:AI_NAME}`` and
``${settings:AI_NAME}`` references, resolved at agent-build time by the shared
:func:`bublik.ai.config.resolve_headers` (same strict ``AI_``-prefix guard as
provider API keys and provider headers); like key rotation, rotating a token
without editing the config does not bust the agent cache (its fingerprint is a
digest of the authored config).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic_ai.mcp import MCPToolset

from bublik.ai.config import resolve_headers


if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset

    from bublik.ai.types import AiConfig


logger = logging.getLogger(__name__)


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
        headers = resolve_headers(server.headers, f'MCP server {server.id!r}')
        if headers is None:
            continue
        try:
            toolsets.append(
                MCPToolset(server.url, headers=headers or None).prefixed(server.id),
            )
        except Exception:
            logger.exception('failed to build MCP toolset for server %r', server.id)
    return toolsets
