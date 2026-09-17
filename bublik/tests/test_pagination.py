# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from bublik.core.pagination import DefaultPageNumberPagination


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
