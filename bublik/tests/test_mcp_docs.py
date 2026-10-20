# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The documentation tools as seen through an MCP client."""

from __future__ import annotations

from pathlib import Path
import tempfile

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase
from django.test.utils import override_settings
from fastmcp import Client, FastMCP

from bublik.mcp import docs, tools


MCP_PAGE = """# MCP Setup

Connect AI agents to Bublik.

## Client Configuration

Add the server definition to your client.
"""


class McpDocsToolsTest(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.docs_dir = Path(tmp.name)
        page = self.docs_dir / 'configuration/mcp.md'
        page.parent.mkdir(parents=True)
        page.write_text(MCP_PAGE, encoding='utf-8')
        page.with_suffix('.html').write_text('<html/>', encoding='utf-8')
        docs.reset_index_cache()
        self.addCleanup(docs.reset_index_cache)
        self.mcp = FastMCP(name='bublik-mcp-test')
        tools.register_tools(self.mcp)

    def _call(self, name, args):
        async def run():
            async with Client(self.mcp) as client:
                return await client.call_tool(name, args, raise_on_error=False)

        return async_to_sync(run)()

    def _tool_names(self):
        async def run():
            async with Client(self.mcp) as client:
                return {tool.name for tool in await client.list_tools()}

        return async_to_sync(run)()

    def test_tools_are_registered(self):
        self.assertTrue({'search_docs', 'get_doc', 'list_docs'} <= self._tool_names())

    def test_search_and_read_with_absolute_urls(self):
        with override_settings(
            BUBLIK_DOCS_STATIC=str(self.docs_dir),
            URL_PREFIX='bublik',
            BUBLIK_FQDN='https://bublik.test',
        ):
            result = self._call('search_docs', {'query': 'client configuration'})
            self.assertFalse(result.is_error)
            hit = result.data['hits'][0]
            self.assertEqual(hit['path'], 'configuration/mcp')
            self.assertEqual(hit['heading'], 'Client Configuration')
            self.assertEqual(hit['url'], 'https://bublik.test/bublik/docs/configuration/mcp')

            page = self._call('get_doc', {'path': hit['path'], 'heading': hit['heading']})
            self.assertFalse(page.is_error)
            self.assertEqual(page.data['title'], 'MCP Setup')
            self.assertTrue(page.data['markdown'].startswith('## Client Configuration'))

            listing = self._call('list_docs', {})
            self.assertEqual([p['path'] for p in listing.data['pages']], ['configuration/mcp'])

    def test_relative_urls_without_fqdn(self):
        with override_settings(
            BUBLIK_DOCS_STATIC=str(self.docs_dir), URL_PREFIX='', BUBLIK_FQDN=''
        ):
            result = self._call('get_doc', {'path': '/docs/configuration/mcp.md'})
            self.assertEqual(result.data['url'], '/docs/configuration/mcp')

    def test_traversal_and_unknown_paths_are_errors(self):
        with override_settings(BUBLIK_DOCS_STATIC=str(self.docs_dir)):
            traversal = self._call('get_doc', {'path': '../../etc/passwd'})
            self.assertTrue(traversal.is_error)
            self.assertIn('Invalid documentation path', traversal.content[0].text)

            unknown = self._call('get_doc', {'path': 'configuration/mcp-setup'})
            self.assertTrue(unknown.is_error)
            self.assertIn('configuration/mcp', unknown.content[0].text)

    def test_undeployed_documentation(self):
        with override_settings(BUBLIK_DOCS_STATIC=str(self.docs_dir / 'missing')):
            result = self._call('search_docs', {'query': 'anything'})
            self.assertFalse(result.is_error)
            self.assertEqual(result.data['hits'], [])
            self.assertIn('not deployed', result.data['message'])

            page = self._call('get_doc', {'path': 'configuration/mcp'})
            self.assertFalse(page.is_error)
            self.assertIn('not deployed', page.data['message'])

    def test_blank_docs_setting_is_undeployed(self):
        with override_settings(BUBLIK_DOCS_STATIC=''):
            result = self._call('list_docs', {})
            self.assertEqual(result.data['pages'], [])
            self.assertIn('not deployed', result.data['message'])
