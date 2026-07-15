# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.core.cache import RunCache
from bublik.data import models
from bublik.tests import classification_data as data


class IssueApiDataMixin:
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.other_project = models.Project.objects.create(name='other')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project)
        cls.ping_failed = data.result(cls.run_row, data.iteration(cls.tree.ping))
        data.result(data.run(cls.other_project), data.iteration(cls.tree.ping))
        cls.admin = data.admin()

    def _url(self, name, *args):
        return reverse(f'api-v2:{name}', args=args)


@data.isolated_caches
class IssuePermissionTest(IssueApiDataMixin, APITestCase):
    """
    Whether an issue or a rule can be changed without a login is judged by
    the projects of the objects changed, whatever `?project=` says.
    """

    def setUp(self):
        patcher = data.allow_without_login(self.other_project.id)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.issue = data.issue(self.project)
        self.other_issue = data.issue(self.other_project, title='other issue')

    def test_create(self):
        url = self._url('issues-list')
        response = self.client.post(
            f'{url}?project={self.other_project.id}',
            {'project': self.project.id, 'title': 'new'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response = self.client.post(
            url,
            {'project': self.other_project.id, 'title': 'new'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_move_to_another_project(self):
        response = self.client.patch(
            self._url('issues-detail', self.other_issue.id),
            {'project': self.project.id},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
        response = self.client.patch(
            self._url('issues-detail', self.other_issue.id),
            {'title': 'renamed'},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK, response.data

    def test_bulk_actions_check_every_project(self):
        url = f'{self._url("issues-close")}?project={self.other_project.id}'
        response = self.client.post(
            url,
            {'ids': [self.issue.id, self.other_issue.id]},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not models.Issue.objects.filter(state=models.IssueState.CLOSED).exists()

        response = self.client.post(url, {'ids': [self.other_issue.id]}, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['updated'] == [self.other_issue.id]

    def test_rules_are_judged_by_their_issue(self):
        url = self._url('issue_rules-list')
        matcher = {
            'test': self.tree.ping.id,
            'category': 'env',
            'parameters': {},
            'verdicts': None,
            'tags': [],
        }
        response = self.client.post(
            f'{url}?project={self.other_project.id}',
            {'issue': self.issue.id, **matcher},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response = self.client.post(
            url,
            {'issue': self.other_issue.id, **matcher},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_admin_may_change_any_project(self):
        data.login(self.client, self.admin)
        response = self.client.post(
            self._url('issues-list'),
            {'project': self.project.id, 'title': 'new'},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data

    def test_user_who_is_not_admin_is_refused(self):
        user = models.User.objects.create(
            email='user@example.com',
            roles=models.UserRoles.USER,
            status=models.UserStatus.ACTIVE,
        )
        data.login(self.client, user)
        response = self.client.post(
            self._url('issues-close'),
            {'ids': [self.issue.id]},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@data.isolated_caches
class IssueLifecycleTest(IssueApiDataMixin, APITestCase):
    def setUp(self):
        data.login(self.client, self.admin)
        self.issue = data.issue(self.project)
        self.rule = data.rule(self.issue, self.tree.ping)
        data.stamp(self.ping_failed, self.rule)

    def test_close_deactivates_rules_and_keeps_stamps(self):
        response = self.client.post(
            self._url('issues-close'),
            {'ids': [self.issue.id, self.issue.id, 999999]},
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'requested': 2,
            'updated': [self.issue.id],
            'unchanged': [],
            'not_found': [999999],
        }
        self.rule.refresh_from_db()
        assert not self.rule.active
        assert self.rule.rule_results.exists()

    def test_close_resets_the_caches_of_the_runs_classified(self):
        RunCache.by_obj(self.run_row, 'stats').data = {'unexpected': 1}
        assert RunCache.by_obj(self.run_row, 'stats').data is not None
        self.client.post(self._url('issues-close'), {'ids': [self.issue.id]}, format='json')
        assert RunCache.by_obj(self.run_row, 'stats').data is None

    def test_only_a_change_of_suppression_resets_the_caches(self):
        requests = (
            ('patch', self._url('issues-detail', self.issue.id), {'description': 'notes'}),
            (
                'patch',
                self._url('issue_rules-detail', self.rule.id),
                {'category': models.IssueCategory.FLAKY},
            ),
            ('post', self._url('issue_rules-deactivate'), {'ids': [self.rule.id]}),
            ('post', self._url('issue_rules-activate'), {'ids': [self.rule.id]}),
            ('patch', self._url('issue_rules-detail', self.rule.id), {'expected': False}),
        )
        reset = []
        for method, url, body in requests:
            RunCache.by_obj(self.run_row, 'stats').data = {'unexpected': 1}
            response = getattr(self.client, method)(url, body, format='json')
            assert response.status_code == status.HTTP_200_OK, response.data
            reset.append(RunCache.by_obj(self.run_row, 'stats').data is None)
        assert reset == [False, False, False, False, True]

    def test_reopen_does_not_reactivate_rules(self):
        self.client.post(self._url('issues-close'), {'ids': [self.issue.id]}, format='json')
        response = self.client.post(
            self._url('issues-reopen'),
            {'ids': [self.issue.id]},
            format='json',
        )
        assert response.data['updated'] == [self.issue.id]
        self.rule.refresh_from_db()
        assert not self.rule.active

        response = self.client.get(self._url('issues-detail', self.issue.id))
        assert response.data['rules_state'] == 'dormant'

    def test_rules_of_closed_issue_cannot_be_activated(self):
        self.client.post(self._url('issues-close'), {'ids': [self.issue.id]}, format='json')
        response = self.client.post(
            self._url('issue_rules-activate'),
            {'ids': [self.rule.id]},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_counters(self):
        data.rule(self.issue, self.tree.reset, active=False)
        # Another rule of the issue stamps the same result, which counts once.
        flaky = data.rule(self.issue, self.tree.ping, category=models.IssueCategory.FLAKY)
        data.stamp(self.ping_failed, flaky)
        response = self.client.get(self._url('issues-detail', self.issue.id))
        counters = {
            field: response.data[field]
            for field in ('rule_count', 'active_rule_count', 'result_count')
        }
        assert counters == {'rule_count': 3, 'active_rule_count': 2, 'result_count': 1}


@data.isolated_caches
class IssueListTest(IssueApiDataMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.flaky = data.issue(cls.project, title='flaky')
        data.rule(cls.flaky, cls.tree.ping, category=models.IssueCategory.FLAKY)
        cls.env = data.issue(cls.project, title='env')
        data.rule(cls.env, cls.tree.ping, category=models.IssueCategory.ENV)
        cls.dormant = data.issue(cls.project, title='dormant')
        data.rule(cls.dormant, cls.tree.ping, category=models.IssueCategory.FLAKY, active=False)
        cls.unruled = data.issue(cls.project, title='unruled')

    def _ids(self, **params):
        response = self.client.get(self._url('issues-list'), params)
        assert response.status_code == status.HTTP_200_OK, response.data
        return {issue['id'] for issue in response.data['results']}

    def test_category_takes_a_list_of_active_rule_categories(self):
        assert self._ids(category='flaky') == {self.flaky.id}
        assert self._ids(category='flaky;env') == {self.flaky.id, self.env.id}

    def test_rules_state(self):
        assert self._ids(rules='dormant;unruled') == {self.dormant.id, self.unruled.id}

    def test_search_by_id(self):
        assert self._ids(search=f'#{self.env.id}') == {self.env.id}
        # Not an ID, so only matched as text.
        assert self._ids(search='#²') == set()

    def test_unknown_values_are_rejected(self):
        for params in ({'category': 'flaky,env'}, {'state': 'bogus'}, {'rules': 'none'}):
            response = self.client.get(self._url('issues-list'), params)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, params
            response = self.client.get(self._url('issues-facets'), params)
            assert response.status_code == status.HTTP_400_BAD_REQUEST, params

    def test_page_past_the_end_is_empty(self):
        response = self.client.get(self._url('issues-list'), {'page': 9, 'page_size': 2})
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'pagination': {'count': 4, 'next': None, 'previous': None},
            'results': [],
        }

    def test_facets_count_each_filter_without_itself(self):
        response = self.client.get(
            self._url('issues-facets'),
            {'project': self.project.id, 'category': 'flaky'},
        )
        assert response.status_code == status.HTTP_200_OK
        # The categories are counted over every issue, the other filters over
        # the flaky ones.
        assert response.data['categories']['flaky'] == 1
        assert response.data['categories']['env'] == 1
        assert response.data['state'] == {'open': 1, 'closed': 0}
        assert response.data['rules'] == {
            'enforced': 1,
            'dormant': 0,
            'deactivated': 0,
            'unruled': 0,
        }

    def test_ordering_by_result_count(self):
        env_rule = self.env.rules.get()
        data.stamp(self.ping_failed, env_rule)
        data.stamp(data.result(self.run_row, data.iteration(self.tree.ping)), env_rule)
        data.stamp(self.ping_failed, self.flaky.rules.get())
        response = self.client.get(self._url('issues-list'), {'ordering': '-result_count'})
        assert response.status_code == status.HTTP_200_OK, response.data
        ids = [issue['id'] for issue in response.data['results']]
        assert ids[:2] == [self.env.id, self.flaky.id]
        assert [issue['result_count'] for issue in response.data['results']] == [2, 1, 0, 0]

    def test_a_page_takes_page_size_issues_of_each_project(self):
        other = [data.issue(self.other_project, title=f'other {i}') for i in range(3)]

        def page(number):
            response = self.client.get(
                self._url('issues-list'),
                {'page': number, 'page_size': 2, 'ordering': 'title'},
            )
            assert response.status_code == status.HTTP_200_OK, response.data
            return response.data

        def links(data):
            pagination = data['pagination']
            return (
                pagination['count'],
                pagination['next'] is not None,
                pagination['previous'] is not None,
            )

        first, second, third = page(1), page(2), page(3)
        assert [issue['id'] for issue in first['results']] == [
            self.dormant.id,
            self.env.id,
            other[0].id,
            other[1].id,
        ]
        assert [issue['id'] for issue in second['results']] == [
            self.flaky.id,
            other[2].id,
            self.unruled.id,
        ]
        # (count, has next, has previous)
        assert links(first) == (7, True, False)
        assert links(second) == (7, False, True)
        assert third == {
            'pagination': {'count': 7, 'next': None, 'previous': None},
            'results': [],
        }

    def test_one_project_is_paged_as_a_plain_list(self):
        response = self.client.get(
            self._url('issues-list'),
            {'project': self.project.id, 'page_size': 3, 'ordering': 'title'},
        )
        assert [issue['id'] for issue in response.data['results']] == [
            self.dormant.id,
            self.env.id,
            self.flaky.id,
        ]
        pagination = response.data['pagination']
        assert (pagination['count'], pagination['next'] is not None) == (4, True)

    def test_a_page_takes_page_size_rules_of_each_project(self):
        other_rule = data.rule(data.issue(self.other_project, title='other'), self.tree.ping)
        response = self.client.get(
            self._url('issue_rules-list'),
            {'page_size': 1, 'ordering': 'created_at'},
        )
        assert response.status_code == status.HTTP_200_OK, response.data
        assert {rule['id'] for rule in response.data['results']} == {
            self.flaky.rules.get().id,
            other_rule.id,
        }
        pagination = response.data['pagination']
        assert (pagination['count'], pagination['next'] is not None) == (4, True)

    def test_rules_filter_by_exact_verdicts(self):
        issue = data.issue(self.project, title='verdicts')
        rules = {
            name: data.rule(issue, self.tree.reset, verdicts=verdicts).id
            for name, verdicts in (
                ('ignored', None),
                ('none', []),
                ('x', ['X']),
                ('xy', ['X', 'Y']),
                ('yx', ['Y', 'X']),
                ('separators', ['a;b', 'c,d']),
            )
        }

        def found(*verdicts):
            response = self.client.get(
                self._url('issue_rules-list'),
                {'issue': issue.id, 'verdicts': list(verdicts)},
            )
            assert response.status_code == status.HTTP_200_OK, response.data
            ids = {rule['id'] for rule in response.data['results']}
            return {name for name, rule_id in rules.items() if rule_id in ids}

        assert found('null') == {'ignored'}
        assert found('[]') == {'none'}
        assert found('["X"]') == {'x'}
        assert found('["X", "Y"]') == {'xy'}
        assert found('["a;b", "c,d"]') == {'separators'}
        assert found('["X"]', '["Y", "X"]', 'null') == {'x', 'yx', 'ignored'}

    def test_rules_verdicts_facet(self):
        issue = data.issue(self.project, title='verdicts')
        for verdicts in (None, [], ['X'], ['X', 'Y'], ['X', 'Y']):
            data.rule(issue, self.tree.reset, verdicts=verdicts)
        response = self.client.get(self._url('issue_rules-facets'), {'issue': issue.id})
        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data['verdicts'] == {'["X", "Y"]': 2, '["X"]': 1, '[]': 1, 'null': 1}

    def test_rules_verdicts_must_be_a_json_list_or_null(self):
        for value in ('X', '"X"', '{"X": 1}', '[1]', '[["X"]]'):
            for view in ('issue_rules-list', 'issue_rules-facets'):
                response = self.client.get(self._url(view), {'verdicts': value})
                assert response.status_code == status.HTTP_400_BAD_REQUEST, (view, value)

    def test_rules_filter_by_disposition(self):
        marker = data.rule(self.unruled, self.tree.reset, expected=None)
        response = self.client.get(self._url('issue_rules-list'), {'expected': 'none'})
        assert [rule['id'] for rule in response.data['results']] == [marker.id]
        response = self.client.get(
            self._url('issue_rules-list'),
            {'expected': 'expected;none', 'ordering': 'created_at'},
        )
        assert [rule['id'] for rule in response.data['results']] == list(
            models.IssueRule.objects.order_by('created_at', '-id').values_list('id', flat=True),
        )
