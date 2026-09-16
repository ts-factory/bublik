# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from starlette.requests import Request

from bublik.ai.app import _run_chat
from bublik.ai.config import resolve_provider_headers
from bublik.ai.types import Provider


class AiChatAppValidationTest(IsolatedAsyncioTestCase):
    @mock.patch('bublik.ai.app.get_raw_ai_config')
    @mock.patch('bublik.ai.app.resolve_user', new_callable=mock.AsyncMock)
    async def test_malformed_thread_id_is_rejected_before_agent_construction(
        self,
        resolve_user,
        get_raw_ai_config,
    ):
        resolve_user.return_value = SimpleNamespace(id=1)
        request = Request(
            {
                'type': 'http',
                'method': 'POST',
                'path': '/api/v2/chat',
                'query_string': b'provider=provider&model=model&thread=not-a-uuid',
                'headers': [],
            }
        )

        response = await _run_chat(request)

        self.assertEqual(response.status_code, 422)
        self.assertIn(b'must be a UUID', response.body)
        get_raw_ai_config.assert_not_called()


class RunModelSettingsTest(IsolatedAsyncioTestCase):
    """`_run_chat` merges provider model settings with the resolved headers."""

    def _provider(self, **kwargs):
        return Provider(
            id='opencode-go',
            type='openai',
            api_url='https://opencode.ai/zen/v1',
            **kwargs,
        )

    def _build(self, provider, thread_id='11111111-2222-3333-4444-555555555555'):
        """The settings `_run_chat` would hand to the run for this provider."""
        headers = resolve_provider_headers(provider, thread_id)
        settings = dict(provider.model_settings)
        if headers:
            settings['extra_headers'] = headers
        return settings or None

    async def test_settings_and_headers_are_combined(self):
        provider = self._provider(
            headers={'x-opencode-session': '${thread_id}'},
            model_settings={'openai_continuous_usage_stats': True},
        )
        settings = self._build(provider)
        self.assertTrue(settings['openai_continuous_usage_stats'])
        self.assertEqual(
            settings['extra_headers'],
            {'x-opencode-session': '11111111-2222-3333-4444-555555555555'},
        )

    async def test_settings_cannot_clobber_the_routing_headers(self):
        # extra_headers is applied last: a config author setting it in
        # model_settings must not drop the session header the gateway needs.
        provider = self._provider(
            headers={'x-opencode-session': '${thread_id}'},
            model_settings={'extra_headers': {'x-opencode-session': 'spoofed'}},
        )
        self.assertEqual(
            self._build(provider)['extra_headers'],
            {'x-opencode-session': '11111111-2222-3333-4444-555555555555'},
        )

    async def test_headers_may_override_the_derived_auth_header(self):
        # The other direction of the guard above, and intentional: `api_key`
        # can only express one auth scheme per provider type, so a gateway that
        # wants another one has nowhere else to put it. `extra_headers` is
        # merged over the SDK's own Authorization header, which is the point.
        provider = self._provider(headers={'Authorization': 'Basic ZGVtbzpzM2tyZXQ='})
        self.assertEqual(
            self._build(provider)['extra_headers'],
            {'Authorization': 'Basic ZGVtbzpzM2tyZXQ='},
        )

    async def test_no_settings_and_no_headers_is_none(self):
        self.assertIsNone(self._build(self._provider()))
