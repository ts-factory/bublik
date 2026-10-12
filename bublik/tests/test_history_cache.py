# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import hashlib
from unittest import mock

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory

from bublik.core.history.dto import (
    HistoryCountsDTO,
    HistoryGroupedDTO,
    HistoryIterationGroupDTO,
    HistoryListDTO,
    HistoryPaginationDTO,
    HistoryResultDTO,
)
from bublik.core.history.services import HistoryService
from bublik.core.history.v2.utils import generate_hashkey
from bublik.core.pagination_helpers import PaginatedResult
from bublik.interfaces.api_v2.history.views import HistoryViewSet


HISTORY_PATH = '/bublik/api/v2/history/'


class HistoryCacheKeyTestCase(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def _key(self, query):
        return generate_hashkey(self.factory.get(f'{HISTORY_PATH}?{query}'))

    def test_repeated_parameters_use_last_value(self):
        self.assertEqual(
            self._key('test_name=test&page=2&page=1'),
            self._key('test_name=test&page=1'),
        )
        self.assertNotEqual(
            self._key('test_name=test&page=1&page=2'),
            self._key('test_name=test&page=1'),
        )

    def test_values_containing_delimiters_do_not_collide(self):
        self.assertNotEqual(
            self._key('test_name=test%25tags%3Dlinux'),
            self._key('test_name=test&tags=linux'),
        )


@override_settings(
    CACHES={
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'history-cache-regression-tests',
        },
    },
)
class HistoryResponseCacheTestCase(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        cache.clear()
        self.addCleanup(cache.clear)
        self.list_service = self.enterContext(
            mock.patch.object(
                HistoryService,
                'get_history',
                side_effect=self._list_data,
            ),
        )
        self.grouped_service = self.enterContext(
            mock.patch.object(
                HistoryService,
                'get_history_grouped',
                side_effect=self._grouped_data,
            ),
        )

    def _list_data(self, **params):
        return self._history_data(**params)

    def _grouped_data(self, **params):
        return self._history_data(grouped=True, **params)

    def _history_data(
        self,
        test_name,
        page=None,
        page_size=None,
        *,
        grouped=False,
        **filters,
    ):
        if not test_name:
            msg = 'No test name specified'
            raise ValidationError(msg)
        paginated = PaginatedResult.paginate_queryset([1, 2, 3], page, page_size)
        results = []
        for result_id in paginated['results']:
            if grouped:
                results.append(
                    HistoryIterationGroupDTO.from_data(
                        {
                            'hash': f'iteration-{result_id}',
                            'iteration_id': result_id,
                            'parameters': [],
                            'results_by_verdicts': [],
                        },
                    ),
                )
            else:
                results.append(
                    HistoryResultDTO.from_data(
                        {
                            'start_date': '2026-10-01T00:00:00',
                            'finish_date': '2026-10-01T00:00:01',
                            'duration': '0:00:01',
                            'obtained_result': {
                                'result_type': 'PASSED',
                                'verdicts': [],
                            },
                            'expected_results': [],
                            'important_tags': [],
                            'relevant_tags': [],
                            'metadata': [test_name, filters.get('search', '')],
                            'parameters': [],
                            'has_error': False,
                            'has_measurements': False,
                            'run_id': 1,
                            'project_id': int(filters.get('project_id') or 1),
                            'project_name': 'test-project',
                            'result_id': result_id,
                            'iteration_id': result_id,
                            'report_config_id': None,
                        },
                    ),
                )
        dto_class = HistoryGroupedDTO if grouped else HistoryListDTO
        return dto_class(
            from_date='2026-10-01',
            to_date='2026-10-02',
            counts=HistoryCountsDTO(1, 3, 3, 3, 0),
            pagination=HistoryPaginationDTO(**paginated['pagination']),
            results=results,
            results_ids=[1, 2, 3],
        )

    def _get(self, query='test_name=test&page=1&page_size=1', *, grouped=False):
        action = 'grouped' if grouped else 'list'
        path = f'{HISTORY_PATH}grouped/' if grouped else HISTORY_PATH
        request = self.factory.get(f'{path}?{query}')
        return HistoryViewSet.as_view({'get': action})(request)

    def test_pages_are_cached_independently(self):
        first = self._get()
        second = self._get('test_name=test&page=2&page_size=1')
        cached_first = self._get()
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data['pagination']['page'], 1)
        self.assertEqual(second.data['pagination']['page'], 2)
        self.assertEqual(first.data['results'][0]['result_id'], 1)
        self.assertEqual(second.data['results'][0]['result_id'], 2)
        self.assertEqual(cached_first.data, first.data)
        self.assertEqual(self.list_service.call_count, 2)

    def test_list_and_grouped_are_isolated_in_both_request_orders(self):
        for order in ((False, True), (True, False)):
            with self.subTest(order=order):
                cache.clear()
                self.list_service.reset_mock()
                self.grouped_service.reset_mock()
                for grouped in order:
                    response = self._get(grouped=grouped)
                    expected_field = 'hash' if grouped else 'obtained_result'
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(expected_field, response.data['results'][0])
                    self.assertEqual(self._get(grouped=grouped).data, response.data)
                self.list_service.assert_called_once()
                self.grouped_service.assert_called_once()

    def test_query_order_and_ui_flags_preserve_cache_hits(self):
        original = self._get()
        reordered = self._get('page_size=1&test_name=test&page=1')
        ui_flags = self._get(
            'test_name=test&page=1&page_size=1&clicksearch=1&subsearch=1',
        )
        self.assertEqual(reordered.data, original.data)
        self.assertEqual(ui_flags.data, original.data)
        self.list_service.assert_called_once()

    def test_projects_and_search_filters_are_isolated(self):
        queries = (
            'test_name=test&project=1&page_size=1',
            'test_name=test&project=2&page_size=1',
            'test_name=test&project=1&page_size=1&search=linux',
            'test_name=other&project=1&page_size=1',
        )
        for query in queries:
            response = self._get(query)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self._get(query).data, response.data)
        self.assertEqual(self.list_service.call_count, len(queries))

    def test_page_size_is_part_of_the_cache_key(self):
        small = self._get()
        large = self._get('test_name=test&page=1&page_size=2')
        self.assertEqual(len(small.data['results']), 1)
        self.assertEqual(len(large.data['results']), 2)
        self.assertEqual(self.list_service.call_count, 2)

    def test_empty_page_does_not_reuse_default_page(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                default = self._get('test_name=test&page_size=1', grouped=grouped)
                self.assertEqual(default.status_code, 200)
                response = self._get(
                    'test_name=test&page_size=1&page=',
                    grouped=grouped,
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn('messages', response.data)

    def test_invalid_pagination_errors_are_not_hidden_or_cached(self):
        self._get()
        for parameter, value, status_code in (
            ('page', 'abc', 400),
            ('page', '0', 400),
            ('page', '99', 404),
            ('page_size', '', 400),
            ('page_size', '0', 400),
        ):
            with self.subTest(parameter=parameter, value=value):
                query = f'test_name=test&page=1&page_size=1&{parameter}={value}'
                for _ in range(2):
                    response = self._get(query)
                    self.assertEqual(response.status_code, status_code)
                    self.assertIn('messages', response.data)
        self.assertEqual(self.list_service.call_count, 11)

    def test_old_cache_entries_are_ignored_and_preserved(self):
        legacy_key = hashlib.md5(b'test_name=test%page_size=1%').hexdigest()
        cache.set(legacy_key, {'legacy': True})
        cache.set('unrelated-key', {'other': True})
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertIn('obtained_result', response.data['results'][0])
        self.list_service.assert_called_once()
        self.assertEqual(cache.get(legacy_key), {'legacy': True})
        self.assertEqual(cache.get('unrelated-key'), {'other': True})
