#!/usr/bin/env python
from __future__ import annotations

import os

import django
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from key_value.aio.stores.disk import DiskStore

from bublik.mcp import tools


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
django.setup()


# Read tools whose result depends on the caller; kept out of the response cache.
USER_SCOPED_TOOL_NAMES: list[str] = []


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(name='bublik-mcp')

    mcp.add_middleware(
        ResponseCachingMiddleware(
            cache_storage=DiskStore(directory='/tmp/bublik-mcp-cache'),
            call_tool_settings={
                'excluded_tools': (
                    tools.MCP_WRITE_TOOL_NAMES
                    + tools.MCP_ADMIN_TOOL_NAMES
                    + tools.MCP_UNCACHED_TOOL_NAMES
                    + USER_SCOPED_TOOL_NAMES
                ),
            },
        ),
    )

    tools.register_tools(mcp)

    return mcp
