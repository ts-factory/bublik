# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data.models import MetaTest, Project, Test
from bublik.tests import classification_data as data


@data.isolated_caches
class TestCommentsApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(name='proj')
        cls.test = Test.objects.create(name='some-test')
        cls.admin = data.admin()

    def setUp(self):
        data.login(self.client, self.admin)

    def _url(self, test_id=None, comment_id=None, project_id=None):
        url = f'/api/v2/tests/{test_id or self.test.id}/comments/'
        if comment_id is not None:
            url += f'{comment_id}/'
        return f'{url}?project={project_id or self.project.id}'

    def _add(self, text='first'):
        response = self.client.post(self._url(), {'comment': text}, format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        return MetaTest.objects.get(test=self.test).meta_id

    def test_a_comment_is_added_replaced_and_deleted(self):
        comment_id = self._add()

        response = self.client.patch(
            self._url(comment_id=comment_id),
            {'comment': 'second'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        replaced = MetaTest.objects.get(test=self.test)
        assert replaced.meta_id != comment_id

        response = self.client.delete(self._url(comment_id=replaced.meta_id))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not MetaTest.objects.filter(test=self.test).exists()

    def test_an_unknown_test_or_project_is_not_found(self):
        for url in (self._url(test_id=999999), self._url(project_id=999999)):
            response = self.client.post(url, {'comment': 'text'}, format='json')
            assert response.status_code == status.HTTP_404_NOT_FOUND, url

    def test_an_unknown_comment_is_not_found(self):
        url = self._url(comment_id=999999)

        response = self.client.patch(url, {'comment': 'text'}, format='json')
        assert response.status_code == status.HTTP_404_NOT_FOUND

        assert self.client.delete(url).status_code == status.HTTP_404_NOT_FOUND

    def test_a_comment_of_another_project_is_not_found(self):
        comment_id = self._add()
        other = Project.objects.create(name='other')

        response = self.client.delete(self._url(comment_id=comment_id, project_id=other.id))

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert MetaTest.objects.filter(test=self.test).exists()
