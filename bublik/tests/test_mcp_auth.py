# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
End-to-end tests for MCP authentication, run through the real ASGI app so the
HTTP middleware is exercised.
"""

from __future__ import annotations

from typing import ClassVar
from unittest import mock

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from django.test.utils import override_settings
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
import httpx
from rest_framework import status

from bublik.core.user_token import UserTokenService
from bublik.data.models import (
    TokenStatus,
    User,
    UserRoles,
    UserToken,
)
from bublik.mcp import tools
from bublik.mcp.auth import build_auth_middleware
from bublik.mcp.server import create_mcp_server


BASE_URL = 'http://mcp.test'
MCP_URL = f'{BASE_URL}/mcp/'

_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}


class McpAuthTestCase(TransactionTestCase):
    """Base class wiring a FastMCP app onto an in-process ASGI client.

    TransactionTestCase: tools query the DB from ``sync_to_async`` worker threads.
    """

    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )

    def _token_for(self, user, name='mcp'):
        return UserTokenService.issue(user, name).value

    def _app(self):
        mcp = create_mcp_server()
        return mcp.http_app(middleware=build_auth_middleware())

    def _client(self, app, token=None):
        def factory(headers=None, auth=None, follow_redirects=True, timeout=None, **kwargs):
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
                headers=headers,
                auth=auth,
                follow_redirects=follow_redirects,
                timeout=timeout,
            )

        headers = {'Authorization': f'Bearer {token}'} if token else None
        return Client(
            transport=StreamableHttpTransport(
                url=MCP_URL,
                headers=headers,
                httpx_client_factory=factory,
            ),
        )

    async def _session(self, app, token, body):
        """Run ``body(client)`` inside a live MCP session against ``app``."""
        async with app.router.lifespan_context(app), self._client(app, token) as client:
            return await body(client)

    def run_session(self, token, body, app=None):
        return async_to_sync(self._session)(app or self._app(), token, body)

    def raw_post(self, payload, headers):
        app = self._app()

        async def call():
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url=BASE_URL,
            ) as http:
                return await http.post(MCP_URL, json=payload, headers=headers)

        return async_to_sync(call)()


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpAnonymousAccessTest(McpAuthTestCase):
    def test_read_tools_still_work_without_a_token(self):
        async def body(client):
            return await client.call_tool('get_server_version', {})

        result = self.run_session(None, body)
        assert result.data is not None


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpBadCredentialTest(McpAuthTestCase):
    INITIALIZE: ClassVar[dict] = {
        'jsonrpc': '2.0',
        'id': 1,
        'method': 'initialize',
        'params': {
            'protocolVersion': '2025-06-18',
            'capabilities': {},
            'clientInfo': {'name': 'test', 'version': '1'},
        },
    }
    ACCEPT = 'application/json, text/event-stream'

    def _post(self, authorization):
        return self.raw_post(
            self.INITIALIZE,
            {'Authorization': authorization, 'Accept': self.ACCEPT},
        )

    def test_an_unknown_token_is_401_not_silent_anonymous_access(self):
        response = self._post('Bearer bpat_totally-made-up')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'Invalid access token' in response.text

    def test_a_revoked_token_says_so(self):
        raw = self._token_for(self.user)
        token = UserToken.objects.get(user=self.user)
        token.revoke(revoked_by=self.user)
        assert token.status == TokenStatus.REVOKED

        response = self._post(f'Bearer {raw}')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'Access token revoked' in response.text

    def test_a_malformed_header_is_401(self):
        response = self._post('Bearer not-a-bublik-token')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert 'Malformed Authorization header' in response.text
        assert '\\"Bearer bpat_...\\"' in response.headers['WWW-Authenticate']

    def test_the_value_is_not_echoed_back(self):
        raw = self._token_for(self.user)
        UserToken.objects.get(user=self.user).revoke(revoked_by=self.user)
        response = self._post(f'Bearer {raw}')
        assert raw not in response.text


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class McpResponseCacheTest(McpAuthTestCase):
    """The response cache must never be shared between callers."""

    def _call_get_version(self, app, token, counter):
        async def body(client):
            return await client.call_tool('get_server_version', {})

        with mock.patch.object(
            tools.ServerService,
            'get_version',
            side_effect=lambda: counter.append(1) or {'version': '1.0'},
        ):
            return self.run_session(token, body, app=app)

    def test_a_cached_read_is_not_shared_between_tokens(self):
        app = self._app()
        calls = []

        token_a = self._token_for(self.user, name='a')
        token_b = self._token_for(self.admin, name='b')

        self._call_get_version(app, token_a, calls)
        assert len(calls) == 1, 'the first call should reach the service'

        # Ensures caching is on, so the checks below are not vacuous.
        self._call_get_version(app, token_a, calls)
        assert len(calls) == 1, 'a repeat call by the same token should be cached'

        expected_after_second_token = 2
        self._call_get_version(app, token_b, calls)
        assert len(calls) == expected_after_second_token, (
            "another token must not receive the first user's entry"
        )

        expected_after_anonymous = 3
        self._call_get_version(app, None, calls)
        assert len(calls) == expected_after_anonymous, (
            'an anonymous caller must not receive a token holder entry'
        )
