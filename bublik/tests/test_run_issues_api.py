# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class RunIssuesApiTest(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project)
        ping = data.iteration(cls.tree.ping)
        cls.first = data.result(cls.run_row, ping)
        cls.second = data.result(cls.run_row, ping)
        cls.loss = data.issue(cls.project, title='Loss', bug_key='ref://JIRA/NET-1')
        cls.expected = data.rule(cls.loss, cls.tree.ping, category='known-issue')
        cls.marker = data.rule(
            cls.loss, cls.tree.ping, category='to-investigate', expected=None
        )
        for result in (cls.first, cls.second):
            data.stamp(result, cls.expected)
        data.stamp(cls.first, cls.marker)
        cls.stale = data.issue(cls.project, title='Stale', state=models.IssueState.CLOSED)
        data.stamp(cls.first, data.rule(cls.stale, cls.tree.ping, category='flaky'))

    def _summary(self, **params):
        response = self.client.get(
            reverse('api-v2:runs-issues', args=[self.run_row.id]), params
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        return response.data

    def test_summary_per_issue(self):
        loss, stale = self._summary()
        assert (loss['issue_id'], loss['result_count']) == (self.loss.id, 2)
        assert loss['bug_key'] == 'ref://JIRA/NET-1'
        assert [(rule['rule_id'], rule['effect']) for rule in loss['rules']] == [
            (self.expected.id, 'suppressed'),
            (self.marker.id, 'marked'),
        ]
        assert (stale['issue_id'], stale['rules'][0]['effect']) == (self.stale.id, 'stale')

    def test_filters_keep_the_counts_of_the_run(self):
        (loss,) = self._summary(effect='marked')
        assert loss['result_count'] == len([self.first, self.second])
        assert [issue['issue_id'] for issue in self._summary(state='closed')] == [self.stale.id]
        assert [issue['issue_id'] for issue in self._summary(search='net-1')] == [self.loss.id]

    def test_unknown_values_are_rejected(self):
        for params in ({'effect': 'bogus'}, {'state': 'bogus'}, {'category': 'bogus'}):
            response = self.client.get(
                reverse('api-v2:runs-issues', args=[self.run_row.id]),
                params,
            )
            assert response.status_code == status.HTTP_400_BAD_REQUEST, params
