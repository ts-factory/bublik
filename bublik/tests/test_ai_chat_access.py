# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The chat resolves its caller from the same credentials as the REST API."""

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase
from rest_framework_simplejwt.tokens import AccessToken
from starlette.requests import Request

from bublik.ai.access import resolve_user
from bublik.core.user_token import UserTokenService
from bublik.data.models import User, UserRoles, UserToken


def _request(headers=None):
    raw_headers = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    return Request(
        {
            'type': 'http',
            'method': 'POST',
            'path': '/api/v2/chat',
            'query_string': b'',
            'headers': raw_headers,
        }
    )


class ChatCallerResolutionTest(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )

    def _resolve(self, headers=None):
        return async_to_sync(resolve_user)(_request(headers))

    def test_a_personal_access_token_identifies_its_owner(self):
        token = UserTokenService.issue(self.admin, 'chat').value

        resolved = self._resolve({'Authorization': f'Bearer {token}'})

        assert resolved == self.admin
        assert resolved.roles == UserRoles.ADMIN

    def test_the_login_cookie_identifies_its_owner(self):
        jwt = str(AccessToken.for_user(self.user))

        resolved = self._resolve({'Cookie': f'access_token={jwt}'})

        assert resolved == self.user

    def test_a_revoked_token_resolves_to_nobody(self):
        token = UserTokenService.issue(self.user, 'chat').value
        UserToken.objects.get(user=self.user).revoke(revoked_by=self.user)

        assert self._resolve({'Authorization': f'Bearer {token}'}) is None

    def test_a_token_wins_over_a_cookie_for_another_user(self):
        token = UserTokenService.issue(self.admin, 'chat').value
        jwt = str(AccessToken.for_user(self.user))

        resolved = self._resolve(
            {'Authorization': f'Bearer {token}', 'Cookie': f'access_token={jwt}'},
        )

        assert resolved == self.admin

    def test_no_credentials_resolve_to_nobody(self):
        assert self._resolve() is None
