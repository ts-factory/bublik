# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Live status of the MCP servers a chat run would attach for a user.

Servers are probed concurrently, the way a run connects them. Results are
reused for a short while, so polling the endpoint does not reconnect each time.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, replace
import logging
import time
from typing import TYPE_CHECKING, Literal

from pydantic_ai.mcp import MCPToolset

from bublik.ai.mcp import (
    ADMIN_INIT_TIMEOUT_S,
    build_mcp_toolsets,
    build_user_mcp_toolsets,
    user_tool_prefix,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from pydantic_ai.toolsets import AbstractToolset

    from bublik.ai.types import AiConfig
    from bublik.data.models import UserMcpServer


logger = logging.getLogger(__name__)

# Extra time so the toolset's own connect timeout fires first.
_PROBE_SLACK_S = 2.0
_ERROR_MAX_LEN = 200
_CACHE_TTL_S = 30.0

# Probe results by server identity: {key: (monotonic time, result)}.
_cache: dict[tuple, tuple[float, ProbeResult]] = {}

Status = Literal['connected', 'unavailable', 'disabled', 'misconfigured', 'collision']


@dataclass(frozen=True)
class ServerStatus:
    """One server as the status endpoint reports it."""

    scope: Literal['global', 'user']
    id: str
    name: str
    status: Status
    tools: int | None = None
    error: str | None = None
    server_id: int | None = None


@dataclass(frozen=True)
class ProbeResult:
    status: Status
    tools: int | None = None
    error: str | None = None


def _unwrap(toolset: AbstractToolset) -> AbstractToolset:
    """The MCPToolset under any wrappers (``.prefixed()`` adds one)."""
    while not isinstance(toolset, MCPToolset) and hasattr(toolset, 'wrapped'):
        toolset = toolset.wrapped
    return toolset


def _describe(exc: BaseException) -> str:
    # anyio wraps connection failures in an ExceptionGroup; report the leaf.
    while (nested := getattr(exc, 'exceptions', None)) and isinstance(nested[0], BaseException):
        exc = nested[0]
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ''
    label = f'{type(exc).__name__}: {text}' if text else type(exc).__name__
    return label[:_ERROR_MAX_LEN]


async def probe_toolset(toolset: AbstractToolset, timeout: float) -> ProbeResult:
    """Connect, list the tools and disconnect; never raises."""
    inner = _unwrap(toolset)

    async def connect_and_list():
        async with inner:
            return await inner.list_tools()

    try:
        tools = await asyncio.wait_for(connect_and_list(), timeout + _PROBE_SLACK_S)
    except TimeoutError:
        return ProbeResult('unavailable', error=f'No answer within {timeout:g}s')
    except Exception as exc:
        return ProbeResult('unavailable', error=_describe(exc))
    return ProbeResult('connected', tools=len(tools))


async def _cached_probe(key, probe, toolset, timeout) -> ProbeResult:
    now = time.monotonic()
    for stale in [k for k, (at, _) in _cache.items() if now - at >= _CACHE_TTL_S]:
        del _cache[stale]
    if key in _cache:
        return _cache[key][1]
    result = await probe(toolset, timeout)
    _cache[key] = (time.monotonic(), result)
    return result


async def collect_status(
    config: AiConfig,
    user_servers: Iterable[UserMcpServer],
    *,
    probe: Callable[[AbstractToolset, float], Awaitable[ProbeResult]] = probe_toolset,
) -> dict[str, list[dict]]:
    """Status of the admin-configured and the user's own servers.

    A global server without a toolset is ``misconfigured``, and its errors are
    not shown. A user's server may also be ``disabled`` or ``collision``, and
    its errors are shown. Each server is probed with the timeout a run uses.
    """
    policy = config.user_mcp_servers
    # (status, toolset, timeout, cache key)
    entries: list[tuple[ServerStatus, AbstractToolset | None, float, tuple]] = []

    global_toolsets = {toolset.prefix: toolset for toolset in build_mcp_toolsets(config)}
    for server in config.mcp_servers:
        entries.append(
            (
                ServerStatus('global', server.id, server.id, 'misconfigured'),
                global_toolsets.get(server.id),
                ADMIN_INIT_TIMEOUT_S,
                ('global', server.model_dump_json()),
            ),
        )

    reserved = config.mcp_server_ids
    user_servers = list(user_servers)
    user_toolsets = {
        toolset.prefix: toolset
        for toolset in build_user_mcp_toolsets(
            [server for server in user_servers if server.enabled],
            reserved_ids=reserved,
            policy=policy,
        )
    }
    for server in user_servers:
        base = ServerStatus(
            'user', server.slug, server.name, 'misconfigured', server_id=server.pk
        )
        if not server.enabled:
            base, toolset = replace(base, status='disabled'), None
        elif server.slug in reserved:
            base, toolset = replace(base, status='collision'), None
        else:
            toolset = user_toolsets.get(user_tool_prefix(server.slug))
        key = ('user', server.pk, str(server.updated), server.url, policy.model_dump_json())
        entries.append((base, toolset, policy.connect_timeout_s, key))

    probed = await asyncio.gather(
        *(
            _cached_probe(key, probe, toolset, timeout)
            for _status, toolset, timeout, key in entries
            if toolset is not None
        )
    )
    results = iter(probed)
    report: dict[str, list[dict]] = {'global': [], 'user': []}
    for status, toolset, _timeout, _key in entries:
        if toolset is not None:
            result = next(results)
            status = replace(
                status,
                status=result.status,
                tools=result.tools,
                error=result.error if status.scope == 'user' else None,
            )
        report[status.scope].append(asdict(status))
    return report
