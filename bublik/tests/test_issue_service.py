# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The issue and rule management services, as the REST API and the MCP tools call them."""

from unittest import mock

from django.test import TestCase
import pytest
from rest_framework.exceptions import ValidationError

from bublik.core.exceptions import NotFoundError
from bublik.core.issue import IssueManagementService, IssueRuleManagementService
from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class IssueManagementServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.admin = data.admin()

    def setUp(self):
        self.issue = data.issue(self.project)
        self.rule = data.rule(self.issue, self.tree.ping)
        self.run_row = data.run(self.project)
        self.result = data.result(self.run_row, data.iteration(self.tree.ping))
        data.stamp(self.result, self.rule)

    def test_get_tells_a_missing_issue(self):
        assert IssueManagementService.get(self.issue.id) == self.issue
        with pytest.raises(NotFoundError):
            IssueManagementService.get(self.issue.id + 1000)
        with pytest.raises(NotFoundError):
            IssueManagementService.get('nope')

    def test_create_and_update_record_the_actor(self):
        serializer = IssueManagementService.validate(
            {'project': self.project.id, 'title': 'new'},
        )
        issue = IssueManagementService.create(serializer, self.admin)
        assert issue.created_by == self.admin

        serializer = IssueManagementService.validate({'title': 'renamed'}, issue)
        issue = IssueManagementService.update(serializer, self.admin)
        assert (issue.title, issue.updated_by) == ('renamed', self.admin)

    def test_close_deactivates_the_rules_and_drops_the_caches(self):
        with mock.patch('bublik.core.issue.services.RunCache') as cache:
            outcome = IssueManagementService.close(
                [self.issue.id, self.issue.id + 1000],
                self.admin,
            )
        assert (outcome.updated, outcome.unchanged, outcome.not_found) == (
            [self.issue.id],
            [],
            [self.issue.id + 1000],
        )
        self.issue.refresh_from_db()
        self.rule.refresh_from_db()
        assert self.issue.state == models.IssueState.CLOSED
        assert self.issue.closed_by == self.admin
        assert not self.rule.active
        assert self.rule.deactivated_by == self.admin
        cache.invalidate_classification_affected.assert_called_once_with([self.run_row.id])

        # Closing again changes nothing.
        assert IssueManagementService.close([self.issue.id], self.admin).unchanged == [
            self.issue.id,
        ]

    def test_reopen_leaves_the_rules_deactivated(self):
        IssueManagementService.close([self.issue.id], self.admin)
        outcome = IssueManagementService.reopen([self.issue.id], self.admin)
        assert outcome.updated == [self.issue.id]
        self.issue.refresh_from_db()
        self.rule.refresh_from_db()
        assert (self.issue.state, self.issue.closed_by) == (models.IssueState.OPEN, None)
        assert not self.rule.active

    def test_delete_takes_the_stamps_and_drops_the_caches(self):
        with mock.patch('bublik.core.issue.services.RunCache') as cache:
            IssueManagementService.delete(self.issue)
        assert not models.RuleResult.objects.filter(result=self.result).exists()
        cache.invalidate_classification_affected.assert_called_once_with([self.run_row.id])


@data.isolated_caches
class IssueRuleManagementServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.admin = data.admin()

    def setUp(self):
        self.issue = data.issue(self.project)
        self.rule = data.rule(self.issue, self.tree.ping)

    def test_activate_refuses_the_rules_of_a_closed_issue(self):
        IssueManagementService.close([self.issue.id], self.admin)
        with pytest.raises(ValidationError, match='reopen the issue first'):
            IssueRuleManagementService.activate([self.rule.id], self.admin)
        IssueManagementService.reopen([self.issue.id], self.admin)
        outcome = IssueRuleManagementService.activate([self.rule.id], self.admin)
        assert outcome.updated == [self.rule.id]
        self.rule.refresh_from_db()
        assert self.rule.active
        assert self.rule.deactivated_by is None

    def test_deactivate_records_the_actor(self):
        outcome = IssueRuleManagementService.deactivate(
            [self.rule.id, self.rule.id + 1000],
            self.admin,
        )
        assert (outcome.updated, outcome.not_found) == ([self.rule.id], [self.rule.id + 1000])
        self.rule.refresh_from_db()
        assert not self.rule.active
        assert self.rule.deactivated_by == self.admin

    def test_only_a_changed_disposition_drops_the_caches(self):
        run_row = data.run(self.project)
        data.stamp(data.result(run_row, data.iteration(self.tree.ping)), self.rule)
        with mock.patch('bublik.core.issue.services.RunCache') as cache:
            serializer = IssueRuleManagementService.validate(
                {'expected': self.rule.expected},
                self.rule,
            )
            IssueRuleManagementService.update(serializer, self.admin)
            cache.invalidate_classification_affected.assert_not_called()

            serializer = IssueRuleManagementService.validate(
                {'expected': not self.rule.expected},
                self.rule,
            )
            IssueRuleManagementService.update(serializer, self.admin)
            cache.invalidate_classification_affected.assert_called_once_with([run_row.id])

    def test_projects_of_skips_missing_rules(self):
        assert IssueRuleManagementService.projects_of([self.rule.id, self.rule.id + 1000]) == [
            self.project.id,
        ]
