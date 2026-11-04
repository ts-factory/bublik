# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data import models
from bublik.tests import classification_data as data


ALL_ACTIONS = ['manage_test_comments', 'read_configs', 'manage_issues']


def per_conf(project, actions):
    return models.Config.objects.create(
        type=models.ConfigTypes.GLOBAL,
        name=models.GlobalConfigs.PER_CONF.name,
        version=0,
        project=project,
        is_active=True,
        content={'NOT_PERMISSION_REQUIRED_ACTIONS': actions},
    )


@data.isolated_caches
class CapabilitiesTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # the first project has no settings of its own, so the global ones apply
        cls.inheriting = models.Project.objects.create(name='inheriting')
        cls.own = models.Project.objects.create(name='own')
        per_conf(None, ['read_configs'])
        per_conf(cls.own, ['manage_issues', 'manage_test_comments'])

    def _capabilities(self):
        response = self.client.get(reverse('api-v2:capabilities-list'))
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.json()

    def test_without_login_the_actions_the_settings_allow(self):
        assert self._capabilities() == {
            'default': ['read_configs'],
            'projects': {
                str(self.inheriting.id): ['read_configs'],
                str(self.own.id): ['manage_test_comments', 'manage_issues'],
            },
        }

    def test_user_is_allowed_no_more_than_without_login(self):
        user = models.User.objects.create(
            email='user@example.com',
            roles=models.UserRoles.USER,
            status=models.UserStatus.ACTIVE,
        )
        expected = self._capabilities()
        data.login(self.client, user)
        assert self._capabilities() == expected

    def test_admin_is_allowed_every_action(self):
        data.login(self.client, data.admin())
        assert self._capabilities() == {
            'default': ALL_ACTIONS,
            'projects': {
                str(self.inheriting.id): ALL_ACTIONS,
                str(self.own.id): ALL_ACTIONS,
            },
        }
