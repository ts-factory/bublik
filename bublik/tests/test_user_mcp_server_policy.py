# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The host policy and the pure validation rules behind users' MCP servers."""

import socket
from unittest import IsolatedAsyncioTestCase, mock

from django.test import SimpleTestCase
import httpx
from rest_framework.exceptions import ValidationError

from bublik.ai.mcp import _PolicyTransport
from bublik.ai.types import AiConfig, UserMcpServersConfig
from bublik.core.user_mcp_server import services
from bublik.core.user_mcp_server.services import (
    classify_host,
    host_allowed,
    merge_headers,
    public_address,
    validate_headers,
    validate_name,
    validate_url,
)
from bublik.data.models import slug_for


def _resolving_to(*addresses):
    return mock.patch.object(
        socket,
        'getaddrinfo',
        return_value=[(None, None, None, None, (address, 0)) for address in addresses],
    )


class PolicyDefaultsTest(SimpleTestCase):
    def test_default_policy_allows_nothing(self):
        policy = AiConfig().user_mcp_servers
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.allowed_hosts, [])
        self.assertFalse(policy.allow_any_public_host)
        self.assertFalse(host_allowed(policy, 'mcp.example.com'))
        self.assertFalse(host_allowed(policy, '8.8.8.8'))

    def test_enabled_by_list_or_switch(self):
        self.assertTrue(UserMcpServersConfig(allowed_hosts=['a.example']).enabled)
        self.assertTrue(UserMcpServersConfig(allow_any_public_host=True).enabled)

    def test_host_listed_exact_and_glob(self):
        policy = UserMcpServersConfig(allowed_hosts=['MCP.Example.com', '*.tools.example'])
        self.assertTrue(policy.host_listed('mcp.example.com'))
        self.assertTrue(policy.host_listed('mcp.example.com.'))
        self.assertTrue(policy.host_listed('a.tools.example'))
        self.assertTrue(policy.host_listed('deep.a.tools.example'))
        self.assertFalse(policy.host_listed('tools.example'))
        self.assertFalse(policy.host_listed('evil-tools.example'))
        self.assertFalse(policy.host_listed('other.example.com'))

    def test_star_lists_every_host(self):
        # A trailing dot is ignored on patterns as on hosts, so `*.` is `*`.
        for pattern in ('*', '*.'):
            everything = UserMcpServersConfig(allowed_hosts=[pattern])
            for host in ('jira.example.com', 'localhost', '10.0.0.1', 'redis'):
                self.assertTrue(host_allowed(everything, host), (pattern, host))
            self.assertTrue(everything.enabled)

    def test_listed_host_is_allowed_even_when_private(self):
        policy = UserMcpServersConfig(allowed_hosts=['redis', '10.0.0.5', 'localhost'])
        with _resolving_to('10.1.2.3'):
            self.assertTrue(host_allowed(policy, 'redis'))
        self.assertTrue(host_allowed(policy, '10.0.0.5'))
        self.assertTrue(host_allowed(policy, 'localhost'))

    def test_allow_any_public_host_still_blocks_private(self):
        policy = UserMcpServersConfig(allow_any_public_host=True)
        self.assertTrue(host_allowed(policy, '93.184.216.34'))
        with _resolving_to('93.184.216.34'):
            self.assertTrue(host_allowed(policy, 'mcp.example.com'))
        for host in (
            'localhost',
            'foo.localhost',
            '127.0.0.1',
            '10.0.0.1',
            '192.168.1.1',
            '169.254.169.254',
            '::1',
            '0.0.0.0',
            'fe80::1',
            'fd00::1',
        ):
            self.assertFalse(host_allowed(policy, host), host)
        # A name with one private address among its answers is private.
        with _resolving_to('93.184.216.34', '10.0.0.9'):
            self.assertFalse(host_allowed(policy, 'redis'))
        with mock.patch.object(socket, 'getaddrinfo', side_effect=socket.gaierror):
            self.assertEqual(classify_host('nope.invalid'), 'unresolved')
            self.assertFalse(host_allowed(policy, 'nope.invalid'))


class ValidationRulesTest(SimpleTestCase):
    def _error(self, func, *args):
        with self.assertRaises(ValidationError) as ctx:
            func(*args)
        return ctx.exception.detail

    def test_slug_for(self):
        self.assertEqual(slug_for('GitHub Copilot MCP'), 'github_copilot_mcp')
        self.assertEqual(slug_for('  --Jira!!  '), 'jira')
        self.assertEqual(slug_for('***'), '')
        self.assertLessEqual(len(slug_for('x' * 100)), 32)

    def test_validate_name(self):
        self.assertEqual(validate_name('  My Server '), ('My Server', 'my_server'))
        self.assertIn('name', self._error(validate_name, '   '))
        self.assertIn('name', self._error(validate_name, '!!!'))
        self.assertIn('name', self._error(validate_name, 'x' * 65))

    def test_validate_url(self):
        policy = UserMcpServersConfig(allowed_hosts=['mcp.example.com'])
        self.assertEqual(
            validate_url(' https://mcp.example.com/mcp ', policy), 'https://mcp.example.com/mcp'
        )
        for bad in (
            '',
            'ftp://mcp.example.com/',
            'mcp.example.com/mcp',
            'https://',
            'https://user:pw@mcp.example.com/',
            'https://mcp.example.com/a b',
            'https://other.example.com/mcp',
        ):
            self.assertIn('url', self._error(validate_url, bad, policy), bad)
        detail = self._error(validate_url, 'https://other.example.com/mcp', policy)
        self.assertIn('allowed_hosts', str(detail['url'][0]))

    def test_refusal_says_why(self):
        policy = UserMcpServersConfig(allow_any_public_host=True)
        with mock.patch.object(socket, 'getaddrinfo', side_effect=socket.gaierror):
            detail = self._error(validate_url, 'https://jira.example.com/mcp', policy)
        self.assertIn('could not be resolved', str(detail['url'][0]))
        detail = self._error(validate_url, 'http://10.0.0.1/mcp', policy)
        self.assertIn('private or loopback', str(detail['url'][0]))
        detail = self._error(validate_url, 'https://x.example.com/', UserMcpServersConfig())
        self.assertIn('is not allowed', str(detail['url'][0]))

    def test_validate_headers(self):
        self.assertEqual(
            validate_headers({'Authorization': 'Bearer x', 'X-Team': 'qa'}),
            {'Authorization': 'Bearer x', 'X-Team': 'qa'},
        )
        for bad in (
            {'Bad Name': 'v'},
            {'': 'v'},
            {'Host': 'evil'},
            {'Content-Length': '0'},
            {'Authorization': ''},
            {'Authorization': 'a\r\nX-Injected: 1'},
            {'Authorization': 'a\nb'},
            {'Authorization': 'x' * 5000},
            {'Authorization': 'Bearer t\u00f6ken'},
            {'A': '1', 'a': '2'},
            {f'H{i}': 'v' for i in range(33)},
        ):
            self.assertIn('headers', self._error(validate_headers, bad), bad)

    def test_merge_headers(self):
        current = {'Authorization': 'old', 'X-Keep': 'k'}
        merged = merge_headers(current, {'authorization': 'new', 'X-Keep': None, 'X-New': 'n'})
        self.assertEqual(merged, {'authorization': 'new', 'X-New': 'n'})
        self.assertEqual(current, {'Authorization': 'old', 'X-Keep': 'k'})

    def test_literal_values_are_never_dereferenced(self):
        # The admin config resolves ${env:AI_*}; user values must stay literal.
        value = '${env:AI_GITHUB_AUTH_TOKEN}'
        self.assertEqual(validate_headers({'Authorization': value}), {'Authorization': value})
        self.assertFalse(hasattr(services, 'resolve_headers'))


class AddressTest(SimpleTestCase):
    def test_shared_nat64_and_mapped_addresses_are_private(self):
        for host in ('100.64.0.1', '64:ff9b::a00:1', '::ffff:10.0.0.1'):
            self.assertEqual(classify_host(host), 'private', host)
        self.assertEqual(classify_host('64:ff9b::808:808'), 'public')

    def test_public_address_is_one_of_the_checked_addresses(self):
        with _resolving_to('93.184.216.34'):
            self.assertEqual(public_address('mcp.example.com'), '93.184.216.34')
        with _resolving_to('93.184.216.34', '10.0.0.1'):
            self.assertIsNone(public_address('mcp.example.com'))
        self.assertIsNone(public_address('localhost'))


class PolicyTransportTest(IsolatedAsyncioTestCase):
    async def _send(self, policy, url):
        sent = []

        async def deliver(_transport, request):
            sent.append(request)
            return httpx.Response(200)

        with mock.patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', deliver):
            async with httpx.AsyncClient(transport=_PolicyTransport(policy)) as client:
                await client.get(url)
        return sent[0]

    async def test_a_public_host_is_pinned_to_the_checked_address(self):
        policy = UserMcpServersConfig(allow_any_public_host=True)
        with _resolving_to('93.184.216.34'):
            request = await self._send(policy, 'https://mcp.example.com/mcp')
        self.assertEqual(request.url.host, '93.184.216.34')
        self.assertEqual(request.headers['host'], 'mcp.example.com')
        self.assertEqual(request.extensions['sni_hostname'], 'mcp.example.com')

    async def test_a_host_that_now_resolves_privately_is_refused(self):
        policy = UserMcpServersConfig(allow_any_public_host=True)
        with _resolving_to('10.0.0.5'), self.assertRaises(httpx.ConnectError):
            await self._send(policy, 'https://mcp.example.com/mcp')

    async def test_a_host_removed_from_the_policy_is_refused(self):
        with self.assertRaises(httpx.ConnectError):
            await self._send(UserMcpServersConfig(), 'https://mcp.example.com/mcp')

    async def test_a_listed_host_is_sent_unchanged(self):
        policy = UserMcpServersConfig(allowed_hosts=['redis'])
        request = await self._send(policy, 'http://redis:8080/mcp')
        self.assertEqual(request.url.host, 'redis')
