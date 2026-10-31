# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data import models
from bublik.tests import classification_data as data


def per_conf(project, actions):
    return models.Config.objects.create(
        type=models.ConfigTypes.GLOBAL,
        name=models.GlobalConfigs.PER_CONF.name,
        version=0,
        project=project,
        is_active=True,
        content={'NOT_PERMISSION_REQUIRED_ACTIONS': actions},
    )


def report_config(project):
    return models.Config.objects.create(
        type=models.ConfigTypes.REPORT,
        name='report',
        version=0,
        project=project,
        is_active=True,
        content={},
    )


@data.isolated_caches
class ConfigReadPermissionTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        # the first project has no settings of its own, so the global ones apply
        cls.inheriting = models.Project.objects.create(name='inheriting')
        cls.restricted = models.Project.objects.create(name='restricted')
        cls.inheriting_report = report_config(cls.inheriting)
        cls.restricted_report = report_config(cls.restricted)
        cls.restricted_per_conf = per_conf(cls.restricted, [])

    def _read(self):
        response = self.client.get(reverse('api-v2:config-list'))
        assert response.status_code == status.HTTP_200_OK, response.data
        return {config['id'] for config in response.data}

    def test_project_settings_override_the_global_ones(self):
        global_per_conf = per_conf(None, ['read_configs'])
        assert self._read() == {global_per_conf.id, self.inheriting_report.id}

    def test_project_settings_may_allow_alone(self):
        global_per_conf = per_conf(None, [])
        self.restricted_per_conf.content = {'NOT_PERMISSION_REQUIRED_ACTIONS': ['read_configs']}
        self.restricted_per_conf.save()
        assert self._read() == {
            global_per_conf.id,
            self.restricted_report.id,
            self.restricted_per_conf.id,
        }

    def test_nothing_without_permission(self):
        per_conf(None, [])
        assert self._read() == set()

    def test_admin_reads_everything(self):
        global_per_conf = per_conf(None, [])
        data.login(self.client, data.admin())
        assert self._read() == {
            global_per_conf.id,
            self.inheriting_report.id,
            self.restricted_report.id,
            self.restricted_per_conf.id,
        }
