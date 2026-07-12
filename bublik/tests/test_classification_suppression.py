# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.core.run.data import is_result_unexpected
from bublik.core.run.stats import get_run_stats
from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class SuppressionTest(TestCase):
    """
    A result stops counting as unexpected only when a stamp of an expected
    rule of a still-open issue is on it.
    """

    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project)
        cls.failed = data.result(cls.run_row, data.iteration(cls.tree.ping))
        cls.other_failed = data.result(cls.run_row, data.iteration(cls.tree.reset))
        cls.open_issue = data.issue(cls.project, title='open')
        cls.closed_issue = data.issue(
            cls.project,
            title='closed',
            state=models.IssueState.CLOSED,
        )

    def _unexpected(self):
        return {
            'stats': get_run_stats(self.run_row.id)['unexpected'],
            'result': is_result_unexpected(self.failed),
            'listed': set(
                models.TestIterationResult.objects.filter(test_run=self.run_row)
                .filter_by_result_classification(['unexpected'])
                .values_list('id', flat=True),
            ),
        }

    def test_without_stamps_both_failures_are_unexpected(self):
        assert self._unexpected() == {
            'stats': 2,
            'result': True,
            'listed': {self.failed.id, self.other_failed.id},
        }

    def test_expected_rule_of_open_issue_suppresses(self):
        data.stamp(self.failed, data.rule(self.open_issue, self.tree.ping, expected=True))
        assert self._unexpected() == {
            'stats': 1,
            'result': False,
            'listed': {self.other_failed.id},
        }
        assert set(
            models.TestIterationResult.objects.filter(test_run=self.run_row)
            .filter_by_result_classification(['expected'])
            .values_list('id', flat=True),
        ) == {self.failed.id}

    def test_deactivated_rule_still_suppresses_its_stamps(self):
        rule = data.rule(self.open_issue, self.tree.ping, expected=True, active=False)
        data.stamp(self.failed, rule)
        assert self._unexpected()['result'] is False

    def test_closed_issue_unexpected_or_marker_rule_does_not_suppress(self):
        for issue, expected in (
            (self.closed_issue, True),
            (self.open_issue, False),
            (self.open_issue, None),
        ):
            data.stamp(self.failed, data.rule(issue, self.tree.ping, expected=expected))
        assert self._unexpected() == {
            'stats': 2,
            'result': True,
            'listed': {self.failed.id, self.other_failed.id},
        }

    def test_conditions_must_hold_for_the_same_stamp(self):
        # An expected rule of a closed issue and an unexpected rule of an open
        # one: neither stamp suppresses, though each condition holds for one.
        data.stamp(self.failed, data.rule(self.closed_issue, self.tree.ping, expected=True))
        data.stamp(self.failed, data.rule(self.open_issue, self.tree.ping, expected=False))
        assert self._unexpected() == {
            'stats': 2,
            'result': True,
            'listed': {self.failed.id, self.other_failed.id},
        }
