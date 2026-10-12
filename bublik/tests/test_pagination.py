# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from bublik.core.exceptions import NotFoundError
from bublik.core.pagination import DefaultPageNumberPagination
from bublik.core.pagination_helpers import PaginatedResult
from bublik.data import models


class DefaultPaginationTest(SimpleTestCase):
    def _page(self, **params):
        request = Request(APIRequestFactory().get('/', params))
        paginator = DefaultPageNumberPagination()
        rows = paginator.paginate_queryset(list(range(5)), request)
        return rows, paginator.get_pagination()

    def test_page(self):
        rows, pagination = self._page(page=2, page_size=2)
        assert rows == [2, 3]
        assert pagination['count'] == len(range(5))

    def test_page_past_the_end_is_empty_with_the_real_count(self):
        rows, pagination = self._page(page=9, page_size=2)
        assert rows == []
        assert dict(pagination) == {'count': 5, 'next': None, 'previous': None}

    def test_invalid_page_is_a_400(self):
        for page in ('x', '0', '-1'):
            with self.assertRaises(ValidationError):
                self._page(page=page)


class PaginatedResultTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        models.Project.objects.bulk_create(
            [models.Project(name=f'project {i}') for i in range(5)],
        )

    def test_queryset_is_counted_and_paged_in_the_database(self):
        queryset = models.Project.objects.order_by('id')
        with self.assertNumQueries(2):
            page = PaginatedResult.paginate_queryset(queryset, page=2, page_size=2)
        assert page['results'] == list(queryset[2:4])
        assert page['pagination'] == {
            'page': 2,
            'count': 5,
            'next': 'page=3',
            'previous': 'page=1',
        }

    def test_page_past_the_end_is_not_found(self):
        with self.assertRaises(NotFoundError):
            PaginatedResult.paginate_queryset(models.Project.objects.all(), page=9)
