# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The MCP classification write tools: who may call them, and what they change."""

from asgiref.sync import async_to_sync
from django.test import TestCase
from fastmcp.exceptions import ToolError
import pytest
from rest_framework.exceptions import ValidationError

from bublik.core.auth import bind_acting_user
from bublik.data import models
from bublik.mcp import tools
from bublik.tests import classification_data as data


def call(tool, **arguments):
    return async_to_sync(tool)(**arguments)


@data.isolated_caches
class ClassificationWriteToolsTest(TestCase):
    """
    The tools are called in-process with the caller bound, as the chat agent
    calls them; the HTTP token path is covered with the other write tools.
    """

    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.other_project = models.Project.objects.create(name='other')
        cls.tree = data.TestTree()
        cls.admin = data.admin()
        cls.user = models.User.objects.create_user(email='u@example.com', password='pw12345!')

    def setUp(self):
        self.run_row = data.run(self.project, tags=[('kernel', '6.1')])
        self.vm = data.result(
            self.run_row,
            data.iteration(self.tree.ping, env='VM'),
            verdicts=['Packet lost'],
        )
        self.hw = data.result(
            self.run_row,
            data.iteration(self.tree.ping, env='HW'),
            verdicts=['Packet lost'],
        )

    def _as(self, user, tool, **arguments):
        with bind_acting_user(user.id):
            return call(tool, **arguments)

    def _matcher(self, **fields):
        return {
            'test_id': self.tree.ping.id,
            'run_ids': [self.run_row.id],
            'parameters': {},
            'verdicts': None,
            'tags': [],
            **fields,
        }

    def test_anonymous_and_unprivileged_callers_are_refused(self):
        arguments = {
            'result_ids': [self.vm.id],
            'categories': ['known-issue'],
            'matchers': [self._matcher()],
            'new_issue': {'title': 'Loss'},
        }
        with pytest.raises(ToolError, match='access token'):
            call(tools.classify_results, **arguments)
        with pytest.raises(ToolError, match='not allowed'):
            self._as(self.user, tools.classify_results, **arguments)
        assert not models.Issue.objects.exists()

    def test_an_open_project_lets_anyone_classify(self):
        with data.allow_without_login(self.project.id):
            outcome = self._as(
                self.user,
                tools.classify_results,
                result_ids=[self.vm.id, self.hw.id],
                categories=['known-issue'],
                matchers=[self._matcher(verdicts=[['Packet lost']], tags=['kernel=6.1'])],
                new_issue={'title': 'Loss', 'bug_key': 'ref://JIRA/NET-1'},
            )
        issue = models.Issue.objects.get(pk=outcome['issue_id'])
        assert (issue.title, issue.created_by) == ('Loss', self.user)
        (rule,) = models.IssueRule.objects.filter(id__in=outcome['rule_ids'])
        assert (rule.verdicts, rule.tags, rule.active) == (
            ['Packet lost'],
            ['kernel=6.1'],
            True,
        )
        assert models.RuleResult.objects.filter(issue_rule=rule).count() == len(
            [self.vm, self.hw]
        )

    def test_the_tools_are_offered_once_some_project_is_open(self):
        assert tools.classify_results not in tools.tools_for(self.user)
        assert tools.classify_results in tools.tools_for(self.admin)
        assert tools.classify_results not in tools.tools_for(None)
        with data.allow_without_login(self.project.id):
            offered = tools.tools_for(self.user)
            assert tools.classify_results in offered
            assert tools.edit_test_comment not in offered
            assert tools.tools_for(None) == tools.MCP_TOOLS

    def test_results_of_several_runs_are_classified_at_once(self):
        # As picked from a test's history.
        later_run = data.run(self.project, tags=[('kernel', '6.1'), ('arch', 'arm')])
        later = data.result(
            later_run,
            data.iteration(self.tree.ping, env='VM'),
            verdicts=['Packet lost'],
        )
        # The later run's important tags contain those of the first, so the
        # results of both are classified by one rule.
        both_runs = [self.run_row.id, later_run.id]
        arguments = {
            'result_ids': [self.vm.id, later.id],
            'categories': ['known-issue'],
            'new_issue': {'title': 'Loss'},
        }
        with pytest.raises(ValidationError, match='arch=arm'):
            self._as(
                self.admin,
                tools.classify_results,
                matchers=[self._matcher(run_ids=both_runs, tags=['arch=arm'])],
                **arguments,
            )
        assert not models.Issue.objects.exists()

        outcome = self._as(
            self.admin,
            tools.classify_results,
            matchers=[
                self._matcher(run_ids=both_runs, parameters={'env': 'VM'}, tags=['kernel=6.1']),
            ],
            **arguments,
        )
        (rule_id,) = outcome['rule_ids']
        stamped = models.RuleResult.objects.filter(issue_rule_id=rule_id)
        assert set(stamped.values_list('result_id', flat=True)) == {self.vm.id, later.id}

        foreign = data.result(
            data.run(self.other_project),
            data.iteration(self.tree.ping),
        )
        with pytest.raises(ValidationError, match='one project'):
            self._as(
                self.admin,
                tools.classify_results,
                result_ids=[self.vm.id, foreign.id],
                categories=['known-issue'],
                matchers=[self._matcher()],
                issue_id=outcome['issue_id'],
            )

    def test_classify_needs_exactly_one_issue_reference(self):
        for extra in ({}, {'issue_id': 1, 'new_issue': {'title': 'x'}}):
            with pytest.raises(ValidationError, match='exactly one'):
                self._as(
                    self.admin,
                    tools.classify_results,
                    result_ids=[self.vm.id],
                    categories=['known-issue'],
                    matchers=[self._matcher()],
                    **extra,
                )

    def test_a_oneoff_classification_is_taken_off_again(self):
        outcome = self._as(
            self.admin,
            tools.classify_results,
            result_ids=[self.vm.id],
            categories=['to-investigate'],
            matchers=[self._matcher()],
            new_issue={'title': 'Look'},
            scope='oneoff',
        )
        (stamp,) = models.RuleResult.objects.filter(result=self.vm)
        assert stamp.origin == models.RuleResultOrigin.MANUAL_ONEOFF
        assert not models.IssueRule.objects.get(pk=outcome['rule_ids'][0]).active

        with pytest.raises(ToolError, match='not allowed'):
            self._as(self.user, tools.unclassify_results, rule_result_ids=[stamp.id])

        taken = self._as(self.admin, tools.unclassify_results, rule_result_ids=[stamp.id])
        assert taken == {
            'deleted_rule_result_ids': [stamp.id],
            'deleted_rule_ids': outcome['rule_ids'],
            'deleted_issue_ids': [outcome['issue_id']],
        }

    def test_the_disposition_is_the_categorys_unless_named(self):
        def classify(**disposition):
            outcome = self._as(
                self.admin,
                tools.classify_results,
                result_ids=[self.vm.id],
                categories=['known-issue'],
                matchers=[self._matcher(parameters={'env': 'VM'})],
                # A rule of its own each time: the same one is reused in an issue.
                new_issue={'title': f'Loss, {disposition}'},
                **disposition,
            )
            return models.IssueRule.objects.get(pk=outcome['rule_ids'][0]).expected

        assert classify() is True
        assert classify(expected='unexpected') is False
        assert classify(expected='none') is None
        with pytest.raises(ValidationError, match='expected, unexpected, none'):
            classify(expected='maybe')

    def test_apply_run_rules_adds_the_missing_stamps(self):
        issue = data.issue(self.project)
        data.rule(issue, self.tree.ping)
        with pytest.raises(ToolError, match='not allowed'):
            self._as(self.user, tools.apply_run_rules, run_id=self.run_row.id)
        assert self._as(self.admin, tools.apply_run_rules, run_id=self.run_row.id) == {
            'stamps_created': len([self.vm, self.hw]),
        }
        assert self._as(self.admin, tools.apply_run_rules, run_id=self.run_row.id) == {
            'stamps_created': 0,
        }
        assert models.RuleResult.objects.filter(created_by=self.admin).exists()

    def test_issue_lifecycle(self):
        created = self._as(
            self.admin,
            tools.edit_issue,
            project_id=self.project.id,
            title='Loss',
            description='packets',
        )
        issue_id = created['id']
        assert (created['title'], created['state']) == ('Loss', 'open')

        edited = self._as(self.admin, tools.edit_issue, issue_id=issue_id, description='')
        assert (edited['description'], edited['updated_by']) == ('', self.admin.id)

        with pytest.raises(ToolError, match='not allowed'):
            self._as(self.user, tools.set_issues_state, issue_ids=[issue_id], state='closed')
        closed = self._as(
            self.admin,
            tools.set_issues_state,
            issue_ids=[issue_id, issue_id + 1000],
            state='closed',
        )
        assert (closed['updated'], closed['not_found']) == ([issue_id], [issue_id + 1000])
        assert models.Issue.objects.get(pk=issue_id).state == models.IssueState.CLOSED
        again = self._as(
            self.admin, tools.set_issues_state, issue_ids=[issue_id], state='closed'
        )
        assert (again['updated'], again['unchanged']) == ([], [issue_id])

        with pytest.raises(ValidationError, match="'open' or 'closed'"):
            self._as(self.admin, tools.set_issues_state, issue_ids=[issue_id], state='done')
        reopened = self._as(
            self.admin, tools.set_issues_state, issue_ids=[issue_id], state='open'
        )
        assert reopened['updated'] == [issue_id]
        assert models.Issue.objects.get(pk=issue_id).state == models.IssueState.OPEN

        assert self._as(self.admin, tools.delete_issue, issue_id=issue_id) == {
            'deleted': issue_id
        }
        assert not models.Issue.objects.filter(pk=issue_id).exists()

    def test_moving_an_issue_needs_both_projects(self):
        issue = data.issue(self.project)
        refused = pytest.raises(ToolError, match='not allowed')
        with data.allow_without_login(self.project.id), refused:
            self._as(
                self.user,
                tools.edit_issue,
                issue_id=issue.id,
                project_id=self.other_project.id,
            )
        issue.refresh_from_db()
        assert issue.project == self.project

    def test_rule_lifecycle(self):
        issue = data.issue(self.project)
        created = self._as(
            self.admin,
            tools.edit_issue_rule,
            issue_id=issue.id,
            test_id=self.tree.ping.id,
            category='flaky',
            parameters={'env': 'VM'},
            ignore_verdicts=True,
            tags=[],
        )
        rule_id = created['id']
        assert (created['verdicts'], created['expected'], created['active']) == (
            None,
            True,
            True,
        )

        def set_expected(expected):
            return self._as(
                self.admin, tools.edit_issue_rule, rule_id=rule_id, expected=expected
            )

        assert set_expected('unexpected')['expected'] is False
        # Not the same as leaving the argument out, which keeps the disposition.
        assert set_expected('none')['expected'] is None
        assert self._as(self.admin, tools.edit_issue_rule, rule_id=rule_id)['expected'] is None
        with pytest.raises(ValidationError, match='expected, unexpected, none'):
            set_expected('maybe')

        def set_active(active):
            return self._as(
                self.admin, tools.set_rules_active, rule_ids=[rule_id], active=active
            )

        assert set_active(False)['updated'] == [rule_id]
        assert set_active(False)['unchanged'] == [rule_id]
        self._as(self.admin, tools.set_issues_state, issue_ids=[issue.id], state='closed')
        with pytest.raises(ValidationError, match='reopen the issue first'):
            set_active(True)
        self._as(self.admin, tools.set_issues_state, issue_ids=[issue.id], state='open')
        assert set_active(True)['updated'] == [rule_id]
        assert models.IssueRule.objects.get(pk=rule_id).active

        with pytest.raises(ToolError, match='not allowed'):
            self._as(self.user, tools.delete_issue_rule, rule_id=rule_id)
        assert self._as(self.admin, tools.delete_issue_rule, rule_id=rule_id) == {
            'deleted': rule_id,
        }

    def test_a_new_rule_needs_its_matcher_in_full(self):
        issue = data.issue(self.project)
        with pytest.raises(ValidationError):
            self._as(
                self.admin,
                tools.edit_issue_rule,
                issue_id=issue.id,
                test_id=self.tree.ping.id,
                category='flaky',
            )
        assert not models.IssueRule.objects.exists()

    def test_the_tools_are_registered_as_writes(self):
        for tool in (tools.classify_results, tools.edit_issue, tools.set_rules_active):
            assert tool in tools.MCP_WRITE_TOOLS
            assert tool.__name__ in tools.MCP_WRITE_TOOL_NAMES
