# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""The MCP classification read tools, called in-process as the chat agent calls them."""

from datetime import datetime, timezone
from unittest import mock

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, TestCase
import pytest
from rest_framework.exceptions import ValidationError

from bublik.core.exceptions import NotFoundError
from bublik.data import models
from bublik.mcp import tools
from bublik.mcp.run.markdown import render_run_leaf_results
from bublik.mcp.run.models import RunLeafResultsPayload
from bublik.tests import classification_data as data


def call(tool, **arguments):
    return async_to_sync(tool)(**arguments)


@data.isolated_caches
class ClassificationReadToolsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project, tags=[('kernel', '6.1')])
        ping = data.iteration(cls.tree.ping, env='VM')
        cls.first = data.result(cls.run_row, ping, verdicts=['Packet lost'])
        cls.second = data.result(cls.run_row, ping, verdicts=['Packet lost'])
        cls.reset = data.result(cls.run_row, data.iteration(cls.tree.reset))
        cls.loss = data.issue(cls.project, title='Packet loss', bug_key='ref://JIRA/NET-1')
        cls.known = data.rule(cls.loss, cls.tree.ping, category='known-issue')
        cls.marker = data.rule(
            cls.loss, cls.tree.ping, category='to-investigate', expected=None
        )
        for result in (cls.first, cls.second):
            data.stamp(result, cls.known)
        cls.marker_stamp = data.stamp(cls.first, cls.marker)
        cls.stale = data.issue(cls.project, title='Stale', state=models.IssueState.CLOSED)
        data.stamp(cls.reset, data.rule(cls.stale, cls.tree.reset, category='flaky'))

    def test_list_issues_filters_and_pages(self):
        listed = call(tools.list_issues, project_id=self.project.id)
        assert [issue['id'] for issue in listed['issues']] == [self.stale.id, self.loss.id]
        assert listed['pagination']['count'] == len([self.stale, self.loss])

        (loss,) = call(tools.list_issues, state='open')['issues']
        assert (loss['title'], loss['result_count']) == ('Packet loss', 2)
        assert {rule['category'] for rule in loss['rules']} == {'known-issue', 'to-investigate'}

        assert [
            i['id'] for i in call(tools.list_issues, search=f'#{self.stale.id}')['issues']
        ] == [
            self.stale.id,
        ]
        known = call(tools.list_issues, category='known-issue')['issues']
        assert [issue['id'] for issue in known] == [self.loss.id]

        with pytest.raises(ValidationError):
            call(tools.list_issues, state='bogus')

    def test_get_issue_lists_every_rule(self):
        got = call(tools.get_issue, issue_id=self.loss.id)
        assert got['issue']['bug_key'] == 'ref://JIRA/NET-1'
        assert sorted(rule['id'] for rule in got['rules']) == sorted(
            [self.known.id, self.marker.id],
        )
        assert {rule['test'] for rule in got['rules']} == {self.tree.ping.id}
        with pytest.raises(NotFoundError):
            call(tools.get_issue, issue_id=self.loss.id + 1000)

    def test_run_issues_summary_and_results(self):
        summary = call(tools.get_run_issues, run_id=self.run_row.id)
        by_title = {row['title']: row for row in summary}
        assert by_title['Packet loss']['result_count'] == len([self.first, self.second])
        assert {rule['effect'] for rule in by_title['Packet loss']['rules']} == {
            'suppressed',
            'marked',
        }
        assert by_title['Stale']['rules'][0]['effect'] == 'stale'
        assert [
            row['title']
            for row in call(tools.get_run_issues, run_id=self.run_row.id, state='closed')
        ] == ['Stale']

        page = call(tools.get_run_issue_results, run_id=self.run_row.id, issue_id=self.loss.id)
        rows = page['results']
        assert {row['result_id'] for row in rows} == {self.first.id, self.second.id}
        first = next(row for row in rows if row['result_id'] == self.first.id)
        assert {issue['rule_result_id'] for issue in first['issues']} >= {self.marker_stamp.id}
        assert first['effective_expected'] is True

    def test_issue_results_across_runs_take_the_listing_filters(self):
        other_run = data.run(self.project)
        data.stamp(data.result(other_run, data.iteration(self.tree.ping)), self.known)

        page = call(tools.get_issue_results, issue_id=self.loss.id)
        assert page['pagination']['count'] == len([self.first, self.second, other_run])

        marked = call(tools.get_issue_results, issue_id=self.loss.id, category='to-investigate')
        assert [row['result_id'] for row in marked['results']] == [self.first.id]

        with pytest.raises(ValidationError):
            call(tools.get_issue_results, issue_id=self.loss.id, classification='bogus')

    def test_classify_options_describe_the_results(self):
        options = call(tools.get_classify_options, result_ids=[self.first.id, self.second.id])
        assert options['run_id'] == self.run_row.id
        assert options['tags']['allowed'] == ['kernel=6.1']
        (test,) = options['tests']
        assert (test['test_id'], test['parameters']) == (self.tree.ping.id, {'env': 'VM'})
        assert test['verdicts'] == [['Packet lost']]

        with pytest.raises(ValidationError, match='one run'):
            call(
                tools.get_classify_options,
                result_ids=[
                    self.first.id,
                    data.result(data.run(self.project), data.iteration(self.tree.ping)).id,
                ],
            )

    def test_history_passes_the_triage_filters_through(self):
        with mock.patch('bublik.mcp.tools.HistoryService.get_history') as get_history:
            get_history.return_value = mock.MagicMock()
            with mock.patch('bublik.mcp.tools.HistoryListResponseSerializer') as serializer:
                serializer.return_value.data = {}
                call(
                    tools.get_history,
                    test_name='ping',
                    issue=str(self.loss.id),
                    categories='known-issue',
                    untriaged=True,
                )
        filters = get_history.call_args.kwargs
        assert (filters['issue'], filters['categories']) == (str(self.loss.id), 'known-issue')
        assert (filters['untriaged'], filters['explained']) == ('true', '')


class LeafResultsIssuesColumnTest(SimpleTestCase):
    def test_the_table_shows_the_stamps(self):
        payload = RunLeafResultsPayload.model_validate(
            {
                'leaf': {'result_id': 1, 'run_id': 2, 'test_name': 'ping', 'path': ['ping']},
                'requirements': None,
                'pagination': {'page': 1, 'count': 1, 'next': None, 'previous': None},
                'results': [
                    {
                        'name': 'ping',
                        'path': 'suite/ping',
                        'result_id': 7,
                        'run_id': 2,
                        'project_id': 1,
                        'project_name': 'proj',
                        'iteration_id': 3,
                        'start': datetime(2026, 1, 1, tzinfo=timezone.utc),
                        'obtained_result': {'result_type': 'FAILED', 'verdicts': []},
                        'expected_results': [],
                        'artifacts': [],
                        'parameters': ['env=VM'],
                        'comments': [],
                        'requirements': [],
                        'has_error': False,
                        'has_measurements': False,
                        'issues': [
                            {
                                'issue_id': 12,
                                'issue_title': 'Packet loss',
                                'issue_state': 'open',
                                'bug_key': None,
                                'bug_url': None,
                                'category': 'known-issue',
                                'expected': True,
                                'rule_result_id': 345,
                                'rule_id': 9,
                                'origin': 'import',
                            },
                        ],
                        'effective_expected': True,
                        'classification': 'expected',
                    },
                ],
            },
        )
        rendered = render_run_leaf_results(payload)
        assert '| Classification | Issues | Verdicts |' in rendered
        assert '#12 Packet loss (open) [known-issue, expected; stamp 345]' in rendered
