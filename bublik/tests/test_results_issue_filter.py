# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class ResultsIssueFilterTest(APITestCase):
    """GET /results/?issue= over results of two runs stamped under two issues."""

    @classmethod
    def setUpTestData(cls):
        project = models.Project.objects.create(name='proj')
        tree = data.TestTree()
        ping = data.iteration(tree.ping)
        first_run, second_run = data.run(project), data.run(project)
        cls.packet_loss = data.issue(project, title='packet loss')
        cls.timeout = data.issue(project, title='timeout')
        packet_loss_rule = data.rule(cls.packet_loss, tree.ping)
        cls.first = data.result(first_run, ping)
        cls.second = data.result(second_run, ping)
        cls.timed_out = data.result(second_run, ping)
        data.result(second_run, ping)
        data.stamp(cls.first, packet_loss_rule)
        data.stamp(cls.second, packet_loss_rule)
        data.stamp(cls.second, data.rule(cls.packet_loss, tree.ping, category='flaky'))
        data.stamp(cls.timed_out, data.rule(cls.timeout, tree.ping))

    def _get(self, issue):
        return self.client.get(reverse('api-v2:results-list'), {'issue': issue})

    def test_results_of_the_issues_across_runs(self):
        response = self._get(str(self.packet_loss.id))
        assert response.status_code == status.HTTP_200_OK
        # A result stamped by two rules of the issue is listed once.
        assert [row['result_id'] for row in response.data['results']] == [
            self.second.id,
            self.first.id,
        ]
        response = self._get(f'{self.packet_loss.id};{self.timeout.id}')
        assert {row['result_id'] for row in response.data['results']} == {
            self.first.id,
            self.second.id,
            self.timed_out.id,
        }

    def test_an_issue_id_that_is_not_a_number_is_rejected(self):
        for issue in ('abc', f'{self.packet_loss.id},{self.timeout.id}', '²'):
            assert self._get(issue).status_code == status.HTTP_400_BAD_REQUEST
