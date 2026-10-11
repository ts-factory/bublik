# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from unittest import mock

from django.test import TestCase
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from bublik.core.auth import check_action_permission_for_projects
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
