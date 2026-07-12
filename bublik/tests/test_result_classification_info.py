# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.core.classification import build_issues_by_result
from bublik.core.run.stats import generate_results_details
from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class ResultClassificationInfoTest(TestCase):
    """
    The rows of a result carry its stamps, and whether one of them makes its
    failure expected.
    """

    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project)
        cls.failed = data.result(cls.run_row, data.iteration(cls.tree.ping))
        cls.passed = data.result(
            cls.run_row,
            data.iteration(cls.tree.reset),
            status='PASSED',
            unexpected=False,
        )
        cls.issue = data.issue(cls.project, title='open')
        cls.rule = data.rule(cls.issue, cls.tree.ping, expected=True)
        cls.stamp = data.stamp(cls.failed, cls.rule)

    def _issue_info(self, issue_state=models.IssueState.OPEN):
        return {
            'issue_id': self.issue.id,
            'issue_title': 'open',
            'issue_state': issue_state,
            'bug_key': None,
            'bug_url': None,
            'category': models.IssueCategory.KNOWN_ISSUE,
            'expected': True,
            'rule_result_id': self.stamp.id,
            'rule_id': self.rule.id,
            'origin': models.RuleResultOrigin.IMPORT,
        }

    def _rows(self):
        results = models.TestIterationResult.objects.filter(
            id__in=[self.failed.id, self.passed.id],
        )
        return {row['result_id']: row for row in generate_results_details(results)}

    def test_result_details(self):
        rows = self._rows()
        failed = {
            field: rows[self.failed.id][field]
            for field in ('issues', 'effective_expected', 'has_error')
        }
        assert failed == {
            'issues': [self._issue_info()],
            'effective_expected': True,
            'has_error': False,
        }
        passed = {
            field: rows[self.passed.id][field]
            for field in ('issues', 'effective_expected', 'has_error')
        }
        assert passed == {'issues': [], 'effective_expected': False, 'has_error': False}

    def test_closed_issue_does_not_make_the_failure_expected(self):
        models.Issue.objects.filter(id=self.issue.id).update(state=models.IssueState.CLOSED)
        failed = self._rows()[self.failed.id]
        assert failed['issues'] == [self._issue_info(issue_state=models.IssueState.CLOSED)]
        assert failed['effective_expected'] is False
        assert failed['has_error'] is True

    def test_history_rows(self):
        assert build_issues_by_result([self.failed.id, self.passed.id]) == {
            self.failed.id: [self._issue_info()],
        }
