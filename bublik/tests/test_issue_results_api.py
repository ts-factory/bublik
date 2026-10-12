# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.core.classification import Effect, effect_for
from bublik.data import models
from bublik.tests import classification_data as data


@data.isolated_caches
class IssueResultsApiTest(APITestCase):
    """
    GET /issues/<id>/results/ and /issues/<id>/results/facets/ over two
    results stamped under one issue and a third stamped under another.
    """

    @classmethod
    def setUpTestData(cls):
        project = models.Project.objects.create(name='proj')
        test = models.Test.objects.create(name='t')
        now = timezone.now()
        cls.run_row = models.TestIterationResult.objects.create(
            start=now,
            finish=now,
            project=project,
        )

        vm_iteration = models.TestIteration.objects.create(test=test, hash='it-vm')
        vm_iteration.test_arguments.set(
            [data.argument('env', 'VM'), data.argument('mtu', '1500')]
        )
        empty_iteration = models.TestIteration.objects.create(test=test, hash='it-empty')
        empty_iteration.test_arguments.set([data.argument('env', '')])

        def result(iteration, start, metas):
            row = models.TestIterationResult.objects.create(
                iteration=iteration,
                test_run=cls.run_row,
                project=project,
                start=start,
                finish=start,
            )
            for meta_type, value in metas:
                models.MetaResult.objects.create(result=row, meta=data.meta(meta_type, value))
            return row

        cls.failed = result(
            vm_iteration,
            now - timedelta(minutes=2),
            [('result', 'FAILED'), ('err', 'x'), ('verdict', 'Packet lost')],
        )
        cls.passed = result(empty_iteration, now - timedelta(minutes=1), [('result', 'PASSED')])
        cls.other = result(
            vm_iteration,
            now,
            [('result', 'FAILED'), ('err', 'x'), ('verdict', 'Timeout')],
        )

        cls.issue = models.Issue.objects.create(project=project, title='issue')
        rule = models.IssueRule.objects.create(
            issue=cls.issue,
            category=models.IssueCategory.TEST_BUG,
            test=test,
        )
        other_issue = models.Issue.objects.create(project=project, title='other')
        other_rule = models.IssueRule.objects.create(
            issue=other_issue,
            category=models.IssueCategory.PRODUCT_DEFECT,
            expected=False,
            test=test,
        )
        for row, stamp in ((cls.failed, rule), (cls.passed, rule), (cls.other, other_rule)):
            models.RuleResult.objects.create(
                result=row,
                issue_rule=stamp,
                origin=models.RuleResultOrigin.IMPORT,
            )

    def _get(self, params=None, suffix='api-v2:issues-results'):
        return self.client.get(reverse(suffix, args=[self.issue.id]), params or {})

    def _ids(self, params=None):
        response = self._get(params)
        assert response.status_code == status.HTTP_200_OK, response.data
        return [row['result_id'] for row in response.data['results']]

    def test_page_lists_the_issues_results_newest_first(self):
        response = self._get()
        assert response.status_code == status.HTTP_200_OK
        assert response.data['pagination'] == {
            'page': 1,
            'count': 2,
            'next': None,
            'previous': None,
        }
        assert 'facets' not in response.data
        assert [row['result_id'] for row in response.data['results']] == [
            self.passed.id,
            self.failed.id,
        ]
        row = response.data['results'][1]
        assert row['run_id'] == self.run_row.id
        assert row['run_tags'] == {'important_tags': [], 'relevant_tags': []}
        assert row['parameters'] == ['env=VM', 'mtu=1500']
        assert row['has_measurements'] is False

    def test_paging(self):
        response = self._get({'page_size': 1, 'page': 2})
        assert response.status_code == status.HTTP_200_OK
        assert [row['result_id'] for row in response.data['results']] == [self.failed.id]
        assert response.data['pagination']['previous'] == 'page=1'

        response = self._get({'page': 3})
        assert response.status_code == status.HTTP_200_OK
        assert response.data['results'] == []
        assert response.data['pagination'] == {
            'page': 3,
            'count': 2,
            'next': None,
            'previous': None,
        }
        assert self._get({'page_size': 5000}).status_code == status.HTTP_400_BAD_REQUEST
        assert self._get({'page': 'x'}).status_code == status.HTTP_400_BAD_REQUEST

    def test_filters(self):
        assert self._ids({'results': 'FAILED'}) == [self.failed.id]
        assert self._ids({'result_properties': 'expected'}) == [self.passed.id]
        assert self._ids({'verdict': ['Packet lost']}) == [self.failed.id]
        assert self._ids({'category': 'test-bug'}) == [self.passed.id, self.failed.id]
        assert self._ids({'category': 'product-defect'}) == []
        assert self._ids({'classification': 'marked'}) == [self.failed.id]
        assert self._ids({'classification': 'no-effect;marked'}) == [
            self.passed.id,
            self.failed.id,
        ]

    def test_parameter_filter_takes_the_facets_form(self):
        assert self._ids({'parameter': ['env=VM']}) == [self.failed.id]
        assert self._ids({'parameter': ['env=VM', 'mtu=1500']}) == [self.failed.id]
        # An empty value is offered as a bare name.
        assert self._ids({'parameter': ['env']}) == [self.passed.id]

    def test_unknown_filter_values_are_rejected(self):
        assert self._get({'results': 'BOGUS'}).status_code == status.HTTP_400_BAD_REQUEST
        response = self._get({'result_properties': 'unexpcted'})
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert self._get({'category': 'bogus'}).status_code == status.HTTP_400_BAD_REQUEST
        response = self._get({'classification': 'untriaged'})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_issue_is_not_found(self):
        response = self.client.get(reverse('api-v2:issues-results', args=['abc']))
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_facets(self):
        response = self._get(suffix='api-v2:issues-results-facets')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {
            'results': ['PASSED', 'FAILED'],
            'result_properties': ['expected', 'unexpected'],
            'verdicts': ['Packet lost'],
            'categories': ['test-bug'],
            'classifications': ['marked', 'no-effect'],
            'artifacts': [],
            'parameters': ['env', 'env=VM', 'mtu=1500'],
            'requirements': [],
        }


@data.isolated_caches
class IssueResultsScopeTest(APITestCase):
    """
    What the filters mean for results that carry stamps of other issues too,
    and for stamps of a closed issue.
    """

    @classmethod
    def setUpTestData(cls):
        project = models.Project.objects.create(name='proj')
        test = models.Test.objects.create(name='t')
        now = timezone.now()
        run = models.TestIterationResult.objects.create(
            start=now,
            finish=now,
            project=project,
        )

        def failed_result(name):
            iteration = models.TestIteration.objects.create(test=test, hash=name)
            row = models.TestIterationResult.objects.create(
                iteration=iteration,
                test_run=run,
                project=project,
                start=now,
                finish=now,
            )
            for meta_type, value in (('result', 'FAILED'), ('err', 'x')):
                models.MetaResult.objects.create(result=row, meta=data.meta(meta_type, value))
            return row

        def stamp(row, issue, category, expected, active=True):
            rule = models.IssueRule.objects.create(
                issue=issue,
                category=category,
                expected=expected,
                active=active,
                test=test,
            )
            models.RuleResult.objects.create(
                result=row,
                issue_rule=rule,
                origin=models.RuleResultOrigin.IMPORT,
            )

        cls.open_issue = models.Issue.objects.create(project=project, title='open')
        cls.other_issue = models.Issue.objects.create(project=project, title='other')
        cls.closed_issue = models.Issue.objects.create(
            project=project,
            title='closed',
            state=models.IssueState.CLOSED,
        )

        # Stamped under two open issues, with different categories.
        cls.shared = failed_result('shared')
        stamp(cls.shared, cls.open_issue, models.IssueCategory.TEST_BUG, None)
        stamp(cls.shared, cls.other_issue, models.IssueCategory.PRODUCT_DEFECT, False)

        # A stamp that says "unexpected", but of an issue that is closed now.
        cls.stale = failed_result('stale')
        stamp(cls.stale, cls.closed_issue, models.IssueCategory.ENV, False, active=False)

    def _ids(self, issue, params):
        response = self.client.get(reverse('api-v2:issues-results', args=[issue.id]), params)
        assert response.status_code == status.HTTP_200_OK, response.data
        return [row['result_id'] for row in response.data['results']]

    def test_category_is_the_category_of_this_issues_rule(self):
        assert self._ids(self.open_issue, {'category': 'test-bug'}) == [self.shared.id]
        # The other issue's rule is in this category, but not this issue's.
        assert self._ids(self.open_issue, {'category': 'product-defect'}) == []
        assert self._ids(self.other_issue, {'category': 'product-defect'}) == [self.shared.id]

    def test_facets_list_only_the_categories_of_this_issues_rules(self):
        response = self.client.get(
            reverse('api-v2:issues-results-facets', args=[self.open_issue.id]),
        )
        assert response.data['categories'] == ['test-bug']

    def test_classification_follows_the_effect_of_the_stamps(self):
        # The same rule as the `effect` of a stamp: a closed issue is stale
        # whatever its disposition says.
        assert effect_for(False, False, models.IssueState.CLOSED) == Effect.STALE
        assert self._ids(self.closed_issue, {'classification': 'stale'}) == [self.stale.id]
        assert self._ids(self.closed_issue, {'classification': 'unexpected'}) == []

    def test_classification_counts_the_stamps_of_other_issues(self):
        # An unexpected stamp of another open issue shows on this issue's row.
        assert self._ids(self.open_issue, {'classification': 'unexpected'}) == [self.shared.id]
        assert self._ids(self.open_issue, {'classification': 'marked'}) == []
