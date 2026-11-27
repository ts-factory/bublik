# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data.models import Config, ConfigTypes, Project
from bublik.tests import classification_data as data


@data.isolated_caches
class ConfigPatchApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name='proj')
        cls.admin = data.admin()

    def setUp(self):
        data.login(self.client, self.admin)
        response = self.client.post(
            '/api/v2/config/',
            {
                'type': ConfigTypes.SCHEDULE,
                'name': 'nightly',
                'project': self.project.id,
                'is_active': True,
                'description': 'first',
                'content': {'note': 'v1'},
            },
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        self.config = Config.objects.get()

    def _patch(self, **fields):
        return self.client.patch(f'/api/v2/config/{self.config.id}/', fields, format='json')

    def test_the_current_name_may_be_repeated(self):
        response = self._patch(name='nightly', description='every night')

        assert response.status_code == status.HTTP_200_OK, response.data
        self.config.refresh_from_db()
        assert self.config.description == 'every night'

    def test_a_refused_content_does_not_keep_the_new_name(self):
        response = self._patch(name='weekly', content='not json')

        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        self.config.refresh_from_db()
        assert self.config.name == 'nightly'
