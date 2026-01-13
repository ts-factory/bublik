#!/usr/bin/env python
import os

import django
from fastmcp import FastMCP
from fastmcp.server.middleware.caching import ResponseCachingMiddleware
from key_value.aio.stores.disk import DiskStore

from bublik.mcp import tools


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
django.setup()


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(name='bublik-mcp')

    mcp.add_middleware(
        ResponseCachingMiddleware(
            cache_storage=DiskStore(directory='/tmp/bublik-mcp-cache'),
        ),
    )

    tools.register_tools(mcp)

    return mcp
