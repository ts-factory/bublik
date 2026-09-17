# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data import models
from bublik.tests import classification_data as data


class PickersDataMixin:
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.other_project = models.Project.objects.create(name='other')
        cls.tree = data.TestTree()
        run_row = data.run(cls.project)
        data.result(run_row, cls.tree.net_iteration, unexpected=False)
        cls.ping_failed = data.result(run_row, data.iteration(cls.tree.ping))
        data.result(data.run(cls.other_project), data.iteration(cls.tree.reset))
        cls.loss = data.issue(cls.project, title='Packet loss', bug_key='ref://JIRA/NET-1')
        cls.closed = data.issue(cls.project, title='Old loss', state=models.IssueState.CLOSED)
        cls.unused = data.issue(cls.project, title='Unused')
        data.stamp(cls.ping_failed, data.rule(cls.loss, cls.tree.ping))


@data.isolated_caches
class IssuePickerTest(PickersDataMixin, APITestCase):
    def _pick(self, **params):
        response = self.client.get(reverse('api-v2:issues_picker-list'), params)
        assert response.status_code == status.HTTP_200_OK, response.data
        return [option['id'] for option in response.data]

    def test_search_by_title_or_bug_key(self):
        assert self._pick(search='loss') == [self.closed.id, self.loss.id]
        assert self._pick(search='net-1') == [self.loss.id]
        assert self._pick(search='loss', state='open') == [self.loss.id]

    def test_without_search_the_recently_used(self):
        assert self._pick(project=self.project.id) == [self.loss.id]

    def test_by_test_id_or_name(self):
        assert self._pick(search='loss', test=self.tree.ping.id) == [self.loss.id]
        assert self._pick(search='loss', test='suite/net/ping') == [self.loss.id]
        assert self._pick(search='loss', test=self.tree.reset.id) == []

    def test_unknown_state_is_rejected(self):
        response = self.client.get(reverse('api-v2:issues_picker-list'), {'state': 'bogus'})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@data.isolated_caches
class TestPickerTest(PickersDataMixin, APITestCase):
    def _pick(self, **params):
        response = self.client.get(reverse('api-v2:tests_picker-list'), params)
        assert response.status_code == status.HTTP_200_OK, response.data
        return [option['path'] for option in response.data]

    def test_search_by_path_among_tests_with_results(self):
        assert self._pick(project=self.project.id, search='net') == ['suite/net/ping']
        assert self._pick(search='net') == ['suite/net/ping', 'suite/net/reset']

    def test_without_search_the_recently_ruled(self):
        assert self._pick(project=self.project.id) == ['suite/net/ping']
        assert self._pick(project=self.other_project.id) == []
