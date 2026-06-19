# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from datetime import datetime, timezone

from django.test import TestCase

from bublik.core.run.stats import generate_results_details
from bublik.data.models import (
    Issue, IssueCategory, IssueRule, Meta, MetaResult, Project,
    ResultClassification, StampOrigin, Test, TestIteration, TestIterationResult,
)


class ResultDetailsClassificationTest(TestCase):
    def test_details_include_effective_expected_and_issues(self):
        project = Project.objects.create(name='p')
        run = TestIterationResult.objects.create(
            iteration=None, test_run=None,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc), project=project,
        )
        test = Test.objects.create(name='t', result_type='T')
        iteration = TestIteration.objects.create(test=test, hash='h')
        result = TestIterationResult.objects.create(
            iteration=iteration, test_run=run,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc), project=project,
        )
        err = Meta.objects.create(type='err', value='FAILED', hash='e')
        MetaResult.objects.create(result=result, meta=err, serial=0)
        res_m = Meta.objects.create(type='result', value='FAILED', hash='r')
        MetaResult.objects.create(result=result, meta=res_m, serial=0)
        issue = Issue.objects.create(title='bug')
        rule = IssueRule.objects.create(
            project=project, issue=issue, test=test,
            category=IssueCategory.KNOWN_ISSUE, expected=True, active=True,
        )
        ResultClassification.objects.create(result=result, rule=rule, origin=StampOrigin.IMPORT)

        details = generate_results_details([result])
        row = details[0]
        self.assertTrue(row['effective_expected'])
        self.assertFalse(row['has_error'])  # suppressed
        self.assertEqual(row['issues'][0]['category'], 'known-issue')
        self.assertEqual(row['issues'][0]['issue_id'], issue.id)


class HistoryClassificationFilterTest(TestCase):
    def _results(self):
        project = Project.objects.create(name='p')
        run = TestIterationResult.objects.create(
            iteration=None, test_run=None,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc), project=project,
        )
        test = Test.objects.create(name='t', result_type='T')
        out = []
        for i in range(2):
            it = TestIteration.objects.create(test=test, hash=f'h{i}')
            r = TestIterationResult.objects.create(
                iteration=it, test_run=run,
                start=datetime(2026, 1, 1, tzinfo=timezone.utc), project=project,
            )
            err = Meta.objects.create(type='err', value='FAILED', hash=f'e{i}')
            MetaResult.objects.create(result=r, meta=err, serial=0)
            out.append(r)
        # classify only the first
        issue = Issue.objects.create(title='bug')
        rule = IssueRule.objects.create(
            project=project, issue=issue, test=test,
            category=IssueCategory.ENV, expected=True, active=True,
        )
        ResultClassification.objects.create(result=out[0], rule=rule, origin=StampOrigin.IMPORT)
        return project, out

    def test_untriaged_filter(self):
        from bublik.core.history.services import HistoryService
        project, results = self._results()
        base = TestIterationResult.objects.filter(id__in=[r.id for r in results])
        filtered = HistoryService._apply_result_filters(
            base, None, None, None, None, None, ';',
            categories=None, issue=None, explained=None, untriaged='true',
        )
        ids = set(filtered.values_list('id', flat=True))
        self.assertEqual(ids, {results[1].id})  # only the unclassified one

    def test_category_filter(self):
        from bublik.core.history.services import HistoryService
        project, results = self._results()
        base = TestIterationResult.objects.filter(id__in=[r.id for r in results])
        filtered = HistoryService._apply_result_filters(
            base, None, None, None, None, None, ';',
            categories='env', issue=None, explained=None, untriaged=None,
        )
        ids = set(filtered.values_list('id', flat=True))
        self.assertEqual(ids, {results[0].id})
