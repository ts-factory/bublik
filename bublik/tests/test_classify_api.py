# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.core.cache import RunCache
from bublik.data import models
from bublik.tests import classification_data as data


class ClassifyDataMixin:
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.other_project = models.Project.objects.create(name='other')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project, tags=[('kernel', '6.1')])
        cls.package = data.result(cls.run_row, cls.tree.net_iteration, unexpected=False)
        cls.vm = data.result(
            cls.run_row,
            data.iteration(cls.tree.ping, env='VM', mode=''),
            verdicts=['Packet lost'],
        )
        cls.hw = data.result(
            cls.run_row,
            data.iteration(cls.tree.ping, env='HW', mode=''),
            verdicts=['Packet lost', 'Timeout'],
        )
        cls.reset = data.result(cls.run_row, data.iteration(cls.tree.reset, env='HW'))
        cls.other_run_result = data.result(
            data.run(cls.project),
            data.iteration(cls.tree.ping),
        )
        cls.admin = data.admin()

    def _url(self, name):
        return reverse(f'api-v2:{name}')

    def _matcher(self, test, parameters=None, verdicts=None, tags=()):
        return {
            'test_id': test.id,
            'parameters': parameters or {},
            'verdicts': verdicts,
            'tags': list(tags),
        }

    def _classify(self, **fields):
        body = {
            'result_ids': [self.vm.id, self.hw.id],
            'issue': {'title': 'Packet loss'},
            'categories': [models.IssueCategory.KNOWN_ISSUE],
            'matchers': [self._matcher(self.tree.ping, {'mode': ''})],
            **fields,
        }
        return self.client.post(self._url('results-classify'), body, format='json')


@data.isolated_caches
class ClassifyOptionsTest(ClassifyDataMixin, APITestCase):
    def test_options_are_what_all_results_of_a_test_have(self):
        response = self.client.post(
            self._url('results-classify-options'),
            {'result_ids': [self.vm.id, self.hw.id, self.reset.id]},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['run_id'] == self.run_row.id
        assert response.data['tags']['allowed'] == ['kernel=6.1']
        tests = {test['test_path']: test for test in response.data['tests']}
        assert tests['suite/net/ping']['parameters'] == {'mode': ''}
        assert tests['suite/net/ping']['verdicts'] == [
            ['Packet lost'],
            ['Packet lost', 'Timeout'],
        ]
        assert tests['suite/net/ping']['result_ids'] == [self.vm.id, self.hw.id]
        assert tests['suite/net/reset']['parameters'] == {'env': 'HW'}
        assert tests['suite/net/reset']['verdicts'] == [[]]


@data.isolated_caches
class ClassifyTest(ClassifyDataMixin, APITestCase):
    def setUp(self):
        data.login(self.client, self.admin)

    def test_creates_the_issue_in_the_results_project(self):
        response = self._classify(
            issue={'title': 'Packet loss', 'bug_key': 'ref://JIRA/NET-1'},
            categories=['known-issue', 'flaky'],
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        issue = models.Issue.objects.get(id=response.data['issue_id'])
        assert (issue.project, issue.title, issue.bug_key) == (
            self.project,
            'Packet loss',
            'ref://JIRA/NET-1',
        )
        rules = models.IssueRule.objects.filter(id__in=response.data['rule_ids'])
        assert sorted(rule.category for rule in rules) == ['flaky', 'known-issue']
        assert all(rule.active for rule in rules)
        assert set(
            models.RuleResult.objects.values_list('result_id', 'origin').distinct(),
        ) == {
            (self.vm.id, models.RuleResultOrigin.MANUAL_PERSISTENT),
            (self.hw.id, models.RuleResultOrigin.MANUAL_PERSISTENT),
        }

    def test_resets_the_caches_of_the_run(self):
        RunCache.by_obj(self.run_row, 'stats').data = {'unexpected': 2}
        assert RunCache.by_obj(self.run_row, 'stats').data is not None
        response = self._classify()
        assert response.status_code == status.HTTP_201_CREATED, response.data
        assert RunCache.by_obj(self.run_row, 'stats').data is None

    def test_repeating_reuses_the_rules(self):
        first = self._classify()
        issue_id = first.data['issue_id']
        second = self._classify(issue=issue_id)
        assert second.status_code == status.HTTP_201_CREATED, second.data
        assert second.data == {'issue_id': issue_id, 'rule_ids': first.data['rule_ids']}

    def test_oneoff_rules_are_inactive(self):
        response = self._classify(scope='oneoff')
        assert response.status_code == status.HTTP_201_CREATED, response.data
        rule = models.IssueRule.objects.get(id=response.data['rule_ids'][0])
        assert not rule.active
        assert rule.rule_results.filter(origin=models.RuleResultOrigin.MANUAL_ONEOFF).count()

    def test_errors_of_a_new_issue_are_under_issue(self):
        data.issue(self.project, title='Packet loss')
        response = self._classify()
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert set(response.data['messages']['issue']) == {'title'}

    def test_unknown_or_foreign_issue_is_rejected(self):
        response = self._classify(issue=999999)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'issue' in response.data['messages']
        response = self._classify(issue=data.issue(self.other_project).id)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'issue' in response.data['messages']

    def test_only_results_of_tests_of_one_run(self):
        for result_ids in (
            [self.vm.id, self.package.id],
            [self.vm.id, self.run_row.id],
            [self.vm.id, self.other_run_result.id],
            [self.vm.id, 999999],
        ):
            response = self._classify(result_ids=result_ids)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, result_ids
            assert 'result_ids' in response.data['messages'], result_ids

    def test_matcher_may_only_take_what_all_results_have(self):
        for matchers in (
            [],
            [self._matcher(self.tree.ping, {'env': 'VM'})],
            [self._matcher(self.tree.ping, verdicts=[['Packet lost']])],
            [self._matcher(self.tree.ping, tags=['lab=eu'])],
            [self._matcher(self.tree.ping), self._matcher(self.tree.reset)],
            [self._matcher(self.tree.ping), self._matcher(self.tree.ping)],
        ):
            response = self._classify(matchers=matchers)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, matchers
        assert not models.Issue.objects.exists()

    def test_matcher_tags_are_not_repeated(self):
        matcher = self._matcher(self.tree.ping, tags=['kernel=6.1', 'kernel=6.1'])
        response = self._classify(matchers=[matcher])
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['messages'] == {
            'matchers': {0: {'tags': ['Repeated values: kernel=6.1.']}},
        }
        assert not models.Issue.objects.exists()

    def test_matcher_verdicts_are_null_or_all_verdict_lists_of_the_results(self):
        created, rejected = status.HTTP_201_CREATED, status.HTTP_400_BAD_REQUEST
        pings, reset = ([self.vm, self.hw], self.tree.ping), ([self.reset], self.tree.reset)
        both = [['Packet lost'], ['Packet lost', 'Timeout']]
        cases = (
            (pings, None, created),
            (pings, both, created),
            (pings, both[::-1], created),
            (pings, [['Packet lost']], rejected),
            (pings, [['Timeout', 'Packet lost'], ['Packet lost']], rejected),
            (pings, [*both, ['Crash']], rejected),
            (pings, [*both, ['Packet lost']], rejected),
            (reset, [[]], created),
            (reset, [['Timeout']], rejected),
            (reset, [], rejected),
        )
        for number, ((results, test), verdicts, expected_status) in enumerate(cases):
            response = self._classify(
                result_ids=[result.id for result in results],
                issue={'title': f'Issue {number}'},
                matchers=[self._matcher(test, verdicts=verdicts)],
            )
            assert response.status_code == expected_status, (verdicts, response.data)

    def test_a_rule_per_verdict_list_stamps_the_results_with_it(self):
        response = self._classify(
            matchers=[
                self._matcher(
                    self.tree.ping, verdicts=[['Packet lost'], ['Packet lost', 'Timeout']]
                ),
            ],
            categories=['known-issue', 'flaky'],
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        rules = [
            models.IssueRule.objects.get(id=rule_id) for rule_id in response.data['rule_ids']
        ]
        assert [(rule.verdicts, rule.category) for rule in rules] == [
            (['Packet lost'], 'known-issue'),
            (['Packet lost'], 'flaky'),
            (['Packet lost', 'Timeout'], 'known-issue'),
            (['Packet lost', 'Timeout'], 'flaky'),
        ]
        stamped = {
            rule.verdicts[-1]: set(rule.rule_results.values_list('result_id', flat=True))
            for rule in rules
        }
        assert stamped == {'Packet lost': {self.vm.id}, 'Timeout': {self.hw.id}}

    def test_ignored_verdicts_make_one_rule_for_all_the_results(self):
        response = self._classify()
        assert response.status_code == status.HTTP_201_CREATED, response.data
        rule = models.IssueRule.objects.get(id=response.data['rule_ids'][0])
        assert rule.verdicts is None
        assert set(rule.rule_results.values_list('result_id', flat=True)) == {
            self.vm.id,
            self.hw.id,
        }

    def test_repeating_with_ignored_verdicts_reuses_the_rule(self):
        first = self._classify(result_ids=[self.hw.id])
        second = self._classify(result_ids=[self.hw.id], issue=first.data['issue_id'])
        assert second.data['rule_ids'] == first.data['rule_ids']

    def test_judged_by_the_project_of_the_results(self):
        self.client.cookies.clear()
        url = f'{self._url("results-classify")}?project={self.other_project.id}'
        body = {
            'result_ids': [self.reset.id],
            'issue': {'title': 'x'},
            'categories': ['env'],
            'matchers': [self._matcher(self.tree.reset)],
        }
        with data.allow_without_login(self.other_project.id):
            response = self.client.post(url, body, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        with data.allow_without_login(self.project.id):
            response = self.client.post(url, body, format='json')
        assert response.status_code == status.HTTP_201_CREATED, response.data
