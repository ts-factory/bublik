#!/usr/bin/env python
from __future__ import annotations

import os

import django
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from key_value.aio.stores.memory import MemoryStore

from bublik.mcp import tools


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
django.setup()

# Per kind of request (tool call, tool list, ...), least recently used first.
CACHE_MAX_ENTRIES = 512


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(name='bublik-mcp')

    # In memory: each server starts with an empty cache that no other process
    # or instance on the host shares. Bounded, since entries are kept per
    # caller token, live for up to an hour and may reach the 1 MB item limit.
    mcp.add_middleware(
        ResponseCachingMiddleware(
            cache_storage=MemoryStore(max_entries_per_collection=CACHE_MAX_ENTRIES),
        ),
    )

    tools.register_tools(mcp)

    return mcp
