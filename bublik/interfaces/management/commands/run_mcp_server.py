# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand

from bublik.mcp.server import create_mcp_server


class Command(BaseCommand):
    help = 'Run the Bublik MCP server with HTTP transport for AI assistant integration'

    def add_arguments(self, parser):
        parser.add_argument(
            '--host',
            default='127.0.0.1',
            help='Host to bind to (default: 127.0.0.1)',
        )
        parser.add_argument(
            '--port',
            type=int,
            default=8000,
            help='Port to bind to (default: 8000)',
        )
        parser.add_argument(
            '--path',
            default='/mcp/',
            help='MCP endpoint path (default: /mcp/)',
        )

    def handle(self, *args, **options):
        self.stdout.write(f"Starting Bublik MCP server on {options['host']}:{options['port']}")

        mcp = create_mcp_server()

        mcp.run(
            transport='streamable-http',
            host=options['host'],
            port=options['port'],
            log_level='INFO',
        )
