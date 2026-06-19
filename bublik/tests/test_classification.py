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
