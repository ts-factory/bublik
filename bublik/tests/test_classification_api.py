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


from datetime import datetime, timezone

from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data.models import (
    ResultClassification,
    StampOrigin,
    TestIteration,
    TestIterationResult,
)


def _auth(client, project):
    # Write actions use @check_action_permission('manage_classifications'); with no
    # NOT_PERMISSION_REQUIRED_ACTIONS config in the test DB the action is admin-only,
    # so authenticate as an admin via the access_token JWT cookie. roles is a scalar
    # CharField; create_superuser already sets roles=UserRoles.ADMIN.
    from rest_framework_simplejwt.tokens import AccessToken

    from bublik.data.models import User

    user = User.objects.create_superuser(email=f'a{project.id}@x.io', password='x')
    client.cookies['access_token'] = str(AccessToken.for_user(user))
    return user


class IssueLifecycleApiTest(APITestCase):
    def setUp(self):
        from bublik.data.models import Issue, IssueCategory, Project, Test

        self.project = Project.objects.create(name='p')
        self.user = _auth(self.client, self.project)
        self.issue = Issue.objects.create(title='bug')
        self.test = Test.objects.create(name='t', result_type='T')
        self.rule = IssueRule.objects.create(
            project=self.project, issue=self.issue, test=self.test,
            category=IssueCategory.KNOWN_ISSUE, expected=True, active=True,
        )

    def test_close_issue_deactivates_rules(self):
        url = f'/api/v2/issues/{self.issue.id}/close/?project={self.project.id}'
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.issue.refresh_from_db()
        self.rule.refresh_from_db()
        self.assertEqual(self.issue.state, 'closed')
        self.assertFalse(self.rule.active)

    def test_deactivate_rule(self):
        url = f'/api/v2/issue-rules/{self.rule.id}/deactivate/?project={self.project.id}'
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.rule.refresh_from_db()
        self.assertFalse(self.rule.active)
