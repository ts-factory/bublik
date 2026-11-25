#!/usr/bin/env python
from __future__ import annotations

import os

from asgiref.sync import sync_to_async
import django
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from key_value.aio.stores.memory import MemoryStore

from bublik.mcp import tools
from bublik.mcp.auth import current_mcp_user


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
django.setup()

# Per kind of request (tool call, tool list, ...), least recently used first.
CACHE_MAX_ENTRIES = 512


def _callable_tool_names() -> set[str]:
    return {tool.__name__ for tool in tools.tools_for(current_mcp_user())}


class CallableToolsMiddleware(Middleware):
    """List only the tools the caller may call in at least one project."""

    async def on_list_tools(self, context, call_next):
        listed = await call_next(context)
        names = await sync_to_async(_callable_tool_names)()
        return [tool for tool in listed if tool.name in names]


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(name='bublik-mcp')

    # Outside the cache: a role or a project's open actions may change.
    mcp.add_middleware(CallableToolsMiddleware())

    # In memory: each server starts with an empty cache that no other process
    # or instance on the host shares. Bounded, since entries are kept per
    # caller token, live for up to an hour and may reach the 1 MB item limit.
    mcp.add_middleware(
        ResponseCachingMiddleware(
            cache_storage=MemoryStore(max_entries_per_collection=CACHE_MAX_ENTRIES),
            # Writes, and the reads showing what they or the browser change.
            call_tool_settings={
                'excluded_tools': (
                    tools.MCP_WRITE_TOOL_NAMES
                    + tools.MCP_ADMIN_TOOL_NAMES
                    + tools.MCP_UNCACHED_TOOL_NAMES
                ),
            },
        ),
    )

    tools.register_tools(mcp)

    return mcp
