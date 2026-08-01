# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, mock

from starlette.requests import Request

from bublik.ai.app import _run_chat


class ChatAppValidationTest(IsolatedAsyncioTestCase):
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
