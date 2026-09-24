# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from starlette.requests import Request

from bublik.ai import mcp_status
from bublik.ai.app import _mcp_status
from bublik.ai.mcp_status import ProbeResult, collect_status, probe_toolset
from bublik.ai.types import AiConfig
from bublik.data.models import UserMcpServer


def _user_server(pk, name, slug, enabled=True):
    server = UserMcpServer(
        name=name, slug=slug, url='https://mcp.example.com/mcp', enabled=enabled
    )
    server.pk = pk
    server.set_headers({'Authorization': 'Bearer x'})
    return server


class CollectStatusTest(IsolatedAsyncioTestCase):
    def setUp(self):
        mcp_status._cache.clear()

    async def test_groups_and_classifies_servers(self):
        config = AiConfig.model_validate(
            {
                'providers': [],
                'mcp_servers': [
                    {'id': 'github', 'url': 'https://gh.example.com/mcp/'},
                    {
                        'id': 'broken',
                        'url': 'https://b.example.com/mcp',
                        'headers': {'Authorization': 'Bearer ${env:AI_MISSING}'},
                    },
                ],
                'user_mcp_servers': {'allowed_hosts': ['*'], 'connect_timeout_s': 3},
            },
        )
        servers = [
            _user_server(1, 'Jira', 'jira'),
            _user_server(2, 'Old', 'old', enabled=False),
            _user_server(3, 'GitHub', 'github'),
            _user_server(4, 'Down', 'down'),
        ]
        probed = []

        async def fake_probe(toolset, timeout):
            probed.append((toolset.prefix, timeout))
            if toolset.prefix == 'down_':
                return ProbeResult('unavailable', error='ConnectError: refused')
            return ProbeResult('connected', tools=4)

        report = await collect_status(config, servers, probe=fake_probe)

        self.assertEqual(sorted(p for p, _t in probed), ['down_', 'github', 'jira_'])
        # Each kind is probed with the connect timeout a run uses for it.
        self.assertEqual(dict(probed), {'down_': 3.0, 'github': 5.0, 'jira_': 3.0})
        self.assertEqual(
            [(s['id'], s['status'], s['tools'], s['error']) for s in report['global']],
            [('github', 'connected', 4, None), ('broken', 'misconfigured', None, None)],
        )
        self.assertEqual(
            [
                (s['id'], s['status'], s['tools'], s['error'], s['server_id'])
                for s in report['user']
            ],
            [
                ('jira', 'connected', 4, None, 1),
                ('old', 'disabled', None, None, 2),
                ('github', 'collision', None, None, 3),
                ('down', 'unavailable', None, 'ConnectError: refused', 4),
            ],
        )
        self.assertEqual(report['user'][0]['name'], 'Jira')

    async def test_repeated_calls_reuse_recent_results(self):
        config = AiConfig.model_validate(
            {
                'providers': [],
                'mcp_servers': [{'id': 'github', 'url': 'https://gh.example/mcp'}],
            },
        )
        calls = []

        async def fake_probe(toolset, timeout):
            calls.append(toolset.prefix)
            return ProbeResult('connected', tools=1)

        await collect_status(config, [], probe=fake_probe)
        await collect_status(config, [], probe=fake_probe)
        self.assertEqual(calls, ['github'])

    async def test_empty(self):
        self.assertEqual(await collect_status(AiConfig(), []), {'global': [], 'user': []})


class ProbeToolsetTest(IsolatedAsyncioTestCase):
    async def test_reports_connection_failure_without_raising(self):
        class Exploding:
            prefix = 'x'

            async def __aenter__(self):
                msg = 'connection refused\nmore detail'
                raise ConnectionError(msg)

            async def __aexit__(self, *_exc):
                return False

        with mock.patch('bublik.ai.mcp_status._unwrap', lambda t: t):
            result = await probe_toolset(Exploding(), 1.0)
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(result.error, 'ConnectionError: connection refused')

    async def test_counts_tools_when_connected(self):
        class Fine:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

            async def list_tools(self):
                return ['a', 'b']

        with mock.patch('bublik.ai.mcp_status._unwrap', lambda t: t):
            result = await probe_toolset(Fine(), 1.0)
        self.assertEqual(result, ProbeResult('connected', tools=2))


class McpStatusRouteTest(IsolatedAsyncioTestCase):
    @mock.patch('bublik.ai.app.resolve_user', new_callable=mock.AsyncMock)
    async def test_requires_authentication(self, resolve_user):
        resolve_user.return_value = None
        request = Request(
            {'type': 'http', 'method': 'GET', 'path': '/api/v2/chat/mcp-status', 'headers': []}
        )
        response = await _mcp_status(request)
        self.assertEqual(response.status_code, 401)

    @mock.patch('bublik.ai.app.collect_status', new_callable=mock.AsyncMock)
    @mock.patch('bublik.ai.app.UserMcpServerService.all_for', return_value=[])
    @mock.patch('bublik.ai.app.get_ai_config', return_value=AiConfig())
    @mock.patch('bublik.ai.app.resolve_user', new_callable=mock.AsyncMock)
    async def test_reports_for_the_caller(self, resolve_user, get_config, all_for, collect):
        resolve_user.return_value = SimpleNamespace(id=7)
        collect.return_value = {'global': [], 'user': []}
        request = Request(
            {'type': 'http', 'method': 'GET', 'path': '/api/v2/chat/mcp-status', 'headers': []}
        )
        response = await _mcp_status(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b'{"global":[],"user":[]}')
        all_for.assert_called_once_with(7)
        get_config.assert_called_once()
