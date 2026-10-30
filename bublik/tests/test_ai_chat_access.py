# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
The chat routes, run through Starlette, so a refused token is seen as the
browser or client sees it.
"""

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
import httpx
from rest_framework import status
from starlette.applications import Starlette

from bublik.ai.app import CHAT_EXCEPTION_HANDLERS, build_chat_routes
from bublik.data.models import User, UserToken


class ChatRoutesTokenTest(TransactionTestCase):
    """TransactionTestCase: the routes query the DB from ``sync_to_async`` threads."""

    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.app = Starlette(
            routes=build_chat_routes(),
            exception_handlers=CHAT_EXCEPTION_HANDLERS,
        )
        self.download = self.app.url_path_for('chat-file-download', file_id='any')

    def _get(self, path, headers):
        async def call():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app),
                base_url='http://chat.test',
            ) as http:
                return await http.get(path, headers=headers)

        return async_to_sync(call)()

    def test_a_revoked_token_is_refused_with_the_reason(self):
        _, raw = UserToken.objects.issue(self.user, 'chat')
        UserToken.objects.get(user=self.user).revoke(revoked_by=self.user)
        response = self._get(
            self.download,
            {'Authorization': f'Bearer {raw}', 'Accept': 'text/html'},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.json()['detail'] == 'Access token revoked'
        assert raw not in response.text

    def test_no_credential_still_bounces_a_browser_to_login(self):
        response = self._get(self.download, {'Accept': 'text/html'})
        assert response.status_code == status.HTTP_302_FOUND
        assert '/auth/login' in response.headers['location']
