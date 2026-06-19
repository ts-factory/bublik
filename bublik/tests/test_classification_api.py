# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.data.models import Issue, IssueCategory, IssueRule, IssueState, Project, Test
from bublik.data.serializers import (
    IssueRuleSerializer,
    IssueSerializer,
)


class IssueSerializerTest(TestCase):
    def test_serialize_issue(self):
        issue = Issue.objects.create(title='bug', description='x', state=IssueState.OPEN)
        data = IssueSerializer(issue).data
        self.assertEqual(data['title'], 'bug')
        self.assertEqual(data['state'], 'open')
        # audit fields are read-only and present
        self.assertIn('created_at', data)


class IssueRuleSerializerTest(TestCase):
    def test_expected_defaults_from_category_when_omitted(self):
        project = Project.objects.create(name='p')
        issue = Issue.objects.create(title='bug')
        test = Test.objects.create(name='t', result_type='T')
        ser = IssueRuleSerializer(data={
            'project': project.id, 'issue': issue.id, 'test': test.id,
            'category': IssueCategory.KNOWN_ISSUE,
            # expected omitted -> should default True for known-issue
            'parameters': {'a': '1'}, 'verdicts': ['boom'], 'tags': [],
        })
        ser.is_valid(raise_exception=True)
        rule = ser.save()
        self.assertTrue(rule.expected)

    def test_expected_explicit_override(self):
        project = Project.objects.create(name='p')
        issue = Issue.objects.create(title='bug')
        test = Test.objects.create(name='t', result_type='T')
        ser = IssueRuleSerializer(data={
            'project': project.id, 'issue': issue.id, 'test': test.id,
            'category': IssueCategory.KNOWN_ISSUE, 'expected': False,
            'parameters': {}, 'verdicts': [], 'tags': [],
        })
        ser.is_valid(raise_exception=True)
        rule = ser.save()
        self.assertFalse(rule.expected)
