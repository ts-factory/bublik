# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.data.models import (
    Issue,
    IssueCategory,
    IssueExt,
    IssueRule,
    IssueState,
    ResultClassification,
    StampOrigin,
    default_expected_for,
)


class ClassificationModelTest(TestCase):
    def test_issue_ext_key_is_unique(self):
        IssueExt.objects.create(key='ref://JIRA/ISSUE-1')
        with self.assertRaises(Exception):
            IssueExt.objects.create(key='ref://JIRA/ISSUE-1')

    def test_issue_defaults_to_open_and_no_ext(self):
        issue = Issue.objects.create(title='flaky thing')
        self.assertEqual(issue.state, IssueState.OPEN)
        self.assertIsNone(issue.issue_ext)

    def test_default_expected_for_category(self):
        self.assertTrue(default_expected_for(IssueCategory.KNOWN_ISSUE))
        self.assertTrue(default_expected_for(IssueCategory.ENV))
        self.assertTrue(default_expected_for(IssueCategory.TEST_BUG))
        self.assertTrue(default_expected_for(IssueCategory.FLAKY))
        self.assertFalse(default_expected_for(IssueCategory.PRODUCT_DEFECT))
        self.assertFalse(default_expected_for(IssueCategory.TO_INVESTIGATE))

    def test_stamp_origin_choices_exist(self):
        self.assertEqual(
            set(StampOrigin.values),
            {'import', 'manual_apply', 'manual_oneoff'},
        )

    def test_result_classification_table_and_relations(self):
        self.assertEqual(ResultClassification._meta.db_table, 'bublik_resultclassification')
        # rule -> stamps reverse accessor, result -> classifications reverse accessor
        self.assertEqual(
            ResultClassification._meta.get_field('rule').remote_field.related_name,
            'stamps',
        )
        self.assertEqual(
            ResultClassification._meta.get_field('result').remote_field.related_name,
            'classifications',
        )


from datetime import datetime, timezone

from bublik.data.models import (
    Meta,
    MetaResult,
    Project,
    Test,
    TestIteration,
    TestIterationResult,
)


class ClassificationFixtureMixin:
    '''Builds a minimal run/result graph and lets a test attach an err meta
    and/or a classification stamp.'''

    _hash_seq = 0

    @classmethod
    def _next_hash(cls, prefix):
        cls._hash_seq += 1
        return f'{prefix}-{cls._hash_seq}'

    def make_project(self):
        return Project.objects.create(name=self._next_hash('proj'))

    def make_result(self, project, *, err=False):
        test = Test.objects.create(name=self._next_hash('test'), result_type='T')
        iteration = TestIteration.objects.create(test=test, hash=self._next_hash('iter'))
        run = TestIterationResult.objects.create(
            iteration=None,
            test_run=None,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project=project,
        )
        result = TestIterationResult.objects.create(
            iteration=iteration,
            test_run=run,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project=project,
        )
        if err:
            err_meta = Meta.objects.create(
                type='err',
                value='FAILED',
                hash=self._next_hash('errmeta'),
            )
            MetaResult.objects.create(result=result, meta=err_meta, serial=0)
        return run, result

    def classify(self, result, project, *, expected, issue_state='open'):
        issue = Issue.objects.create(title='cause', state=issue_state)
        rule = IssueRule.objects.create(
            project=project,
            issue=issue,
            category=IssueCategory.KNOWN_ISSUE,
            expected=expected,
            test=result.iteration.test,
        )
        return ResultClassification.objects.create(
            result=result,
            rule=rule,
            origin=StampOrigin.MANUAL_ONEOFF,
        )


class FixtureSmokeTest(ClassificationFixtureMixin, TestCase):
    def test_make_result_with_err(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=True)
        self.assertTrue(result.meta_results.filter(meta__type='err').exists())

    def test_classify_creates_stamp(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=True)
        self.classify(result, project, expected=True)
        self.assertEqual(result.classifications.count(), 1)


from bublik.core.run.data import is_result_unexpected


class IsResultUnexpectedTest(ClassificationFixtureMixin, TestCase):
    def test_err_without_stamp_is_unexpected(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=True)
        self.assertTrue(is_result_unexpected(result))

    def test_no_err_is_not_unexpected(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=False)
        self.assertFalse(is_result_unexpected(result))

    def test_expected_open_issue_suppresses(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=True)
        self.classify(result, project, expected=True, issue_state='open')
        self.assertFalse(is_result_unexpected(result))

    def test_not_expected_rule_does_not_suppress(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=True)
        self.classify(result, project, expected=False, issue_state='open')
        self.assertTrue(is_result_unexpected(result))

    def test_closed_issue_does_not_suppress(self):
        project = self.make_project()
        _run, result = self.make_result(project, err=True)
        self.classify(result, project, expected=True, issue_state='closed')
        self.assertTrue(is_result_unexpected(result))
