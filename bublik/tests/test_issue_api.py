# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

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
        response = self.client.post(
            f'{url}?project={self.other_project.id}',
            {'issue': self.issue.id, 'test': self.tree.ping.id, 'category': 'env'},
            format='json',
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        response = self.client.post(
            url,
            {'issue': self.other_issue.id, 'test': self.tree.ping.id, 'category': 'env'},
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


@data.isolated_caches
class ApplyRulesApiTest(IssueApiDataMixin, APITestCase):
    def setUp(self):
        data.rule(data.issue(self.project), self.tree.ping)

    def test_judged_by_the_project_of_the_run(self):
        url = self._url('runs-apply-rules', self.run_row.id)
        with data.allow_without_login(self.other_project.id):
            response = self.client.post(f'{url}?project={self.other_project.id}')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not models.RuleResult.objects.exists()

    def test_adds_missing_stamps(self):
        data.login(self.client, self.admin)
        url = self._url('runs-apply-rules', self.run_row.id)
        response = self.client.post(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'stamps_created': 1}
        assert self.client.post(url).data == {'stamps_created': 0}

    def test_a_result_is_not_a_run(self):
        data.login(self.client, self.admin)
        response = self.client.post(self._url('runs-apply-rules', self.ping_failed.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND
