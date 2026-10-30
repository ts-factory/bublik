# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from unittest import mock

from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from bublik.core.auth import check_action_permission_for_projects, get_request_user
from bublik.core.authentication import PersonalTokenAuthentication
from bublik.core.config.services import ConfigServices
from bublik.data import models


ALLOWED_PROJECT, OTHER_PROJECT = 1, 2


def not_permission_required_actions(config_name, data_key, project_id=None, **kwargs):
    return ['manage_issues'] if project_id == ALLOWED_PROJECT else []


class CheckActionPermissionForProjectsTest(TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            ConfigServices,
            'getattr_from_global',
            side_effect=not_permission_required_actions,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _request(self, user=None):
        request = APIRequestFactory().post('/')
        if user is not None:
            request.COOKIES['access_token'] = str(AccessToken.for_user(user))
        return Request(request)

    def _check(self, project_ids, user=None):
        check_action_permission_for_projects(self._request(user), 'manage_issues', project_ids)

    def test_allowed_only_if_every_project_allows(self):
        self._check([ALLOWED_PROJECT, ALLOWED_PROJECT])
        for project_ids in ([OTHER_PROJECT], [ALLOWED_PROJECT, OTHER_PROJECT], []):
            with self.assertRaises(PermissionDenied):
                self._check(project_ids)

    def test_admin_is_always_allowed(self):
        admin = models.User.objects.create(
            email='admin@example.com',
            roles=models.UserRoles.ADMIN,
            status=models.UserStatus.ACTIVE,
        )
        self._check([ALLOWED_PROJECT, OTHER_PROJECT], user=admin)


class GetRequestUserTest(TestCase):
    def test_an_anonymous_request_is_resolved_once(self):
        request = Request(APIRequestFactory().get('/'))
        with mock.patch('bublik.core.auth.resolve_user', return_value=None) as resolve:
            assert get_request_user(request) is None
            assert get_request_user(request) is None
        assert resolve.call_count == 1


class AdminApiLoginSessionTest(APITestCase):
    """User administration takes the login cookie only."""

    def setUp(self):
        self.admin = models.User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=models.UserRoles.ADMIN,
        )
        self.user = models.User.objects.create_user(email='a@example.com', password='pw12345!')
        _, self.raw_token = models.UserToken.objects.issue(self.admin, 'admin')

    def _update_password(self, password, **credentials):
        return self.client.post(
            reverse('api-v2:admin-update-user'),
            {'email': self.user.email, 'password': password},
            format='json',
            **credentials,
        )

    def test_a_token_cannot_administer_users(self):
        bearer = {'HTTP_AUTHORIZATION': f'Bearer {self.raw_token}'}
        # With PersonalTokenAuthentication (bare-metal) and without it (Docker),
        # the token is then resolved by the authenticator or by the view.
        for classes in (
            [PersonalTokenAuthentication, SessionAuthentication],
            [SessionAuthentication],
        ):
            patched = mock.patch.object(APIView, 'authentication_classes', classes)
            with self.subTest(authentication_classes=classes), patched:
                response = self._update_password('Changed-by-token-1!', **bearer)
                assert response.status_code == status.HTTP_403_FORBIDDEN
                assert 'login session' in str(response.data)
                self.user.refresh_from_db()
                assert not self.user.check_password('Changed-by-token-1!')

                response = self.client.get(reverse('api-v2:admin-list'), **bearer)
                assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_the_login_cookie_administers_users(self):
        self.client.cookies['access_token'] = str(AccessToken.for_user(self.admin))
        response = self._update_password('Changed-by-cookie-1!')
        assert response.status_code == status.HTTP_200_OK, response.data
        self.user.refresh_from_db()
        assert self.user.check_password('Changed-by-cookie-1!')
