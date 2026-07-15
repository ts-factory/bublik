# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.test import TestCase
from rest_framework.exceptions import ValidationError

from bublik.core.history.services import HistoryService
from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class HistoryClassificationFiltersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        run_row = data.run(cls.project)
        ping = data.iteration(cls.tree.ping)
        cls.flaky = data.result(run_row, ping)
        cls.known = data.result(run_row, ping)
        cls.untriaged = data.result(run_row, ping)
        cls.passed = data.result(run_row, ping, status='PASSED', unexpected=False)
        cls.issue = data.issue(cls.project, title='flaky')
        cls.other_issue = data.issue(cls.project, title='known')
        data.stamp(cls.flaky, data.rule(cls.issue, cls.tree.ping, category='flaky'))
        data.stamp(cls.known, data.rule(cls.other_issue, cls.tree.ping, category='known-issue'))
        cls.results = models.TestIterationResult.objects.filter(test_run=run_row)

    def _ids(self, **filters):
        return set(
            HistoryService._apply_result_filters(
                test_results=self.results,
                result_statuses=None,
                verdict=None,
                verdict_lookup=None,
                verdict_expr=None,
                result_types=None,
                query_delimiter=settings.QUERY_DELIMITER,
                **filters,
            ).values_list('id', flat=True),
        )

    def test_by_categories_and_issue(self):
        assert self._ids(categories='flaky') == {self.flaky.id}
        assert self._ids(categories='flaky;known-issue') == {self.flaky.id, self.known.id}
        assert self._ids(issue=str(self.issue.id)) == {self.flaky.id}

    def test_categories_and_issue_hold_for_one_stamp(self):
        # Also stamped by the other issue, in the category the issue doesn't use.
        data.stamp(
            self.flaky,
            data.rule(self.other_issue, self.tree.ping, category='known-issue'),
        )
        assert self._ids(issue=str(self.issue.id), categories='known-issue') == set()
        assert self._ids(issue=str(self.issue.id), categories='flaky') == {self.flaky.id}

    def test_explained_and_untriaged(self):
        assert self._ids(explained='true') == {self.flaky.id, self.known.id}
        assert self._ids(untriaged='true') == {self.untriaged.id}
        assert self._ids(untriaged='false') == {
            self.flaky.id,
            self.known.id,
            self.untriaged.id,
            self.passed.id,
        }

    def test_invalid_values_are_rejected(self):
        for filters in (
            {'categories': 'bogus'},
            {'issue': 'abc'},
            {'issue': '²'},
            {'explained': 'yes'},
        ):
            with self.assertRaises(ValidationError):
                self._ids(**filters)
