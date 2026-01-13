# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.core.cache import cache
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.filter_backends import ProjectFilterBackend
from bublik.core.history.services import HistoryService
from bublik.core.history.v2.utils import generate_hashkey


__all__ = [
    'HistoryViewSet',
]


class HistoryViewSet(ListModelMixin, GenericViewSet):
    filter_backends: typing.ClassVar[list] = [ProjectFilterBackend]

    def _extract_query_params(self, request):
        return {
            'test_name': request.query_params.get('test_name'),
            'from_date': request.query_params.get('from_date', ''),
            'to_date': request.query_params.get('to_date', ''),
            'run_ids': request.query_params.get('run_ids', ''),
            'branches': request.query_params.get('branches', ''),
            'revisions': request.query_params.get('revisions', ''),
            'labels': request.query_params.get('labels', ''),
            'tags': request.query_params.get('tags', ''),
            'branch_expr': request.query_params.get('branch_expr', ''),
            'rev_expr': request.query_params.get('rev_expr', ''),
            'label_expr': request.query_params.get('label_expr', ''),
            'tag_expr': request.query_params.get('tag_expr', ''),
            'run_properties': request.query_params.get('run_properties', ''),
            'hash': request.query_params.get('hash', ''),
            'test_args': request.query_params.get('test_args', []),
            'test_arg_expr': request.query_params.get('test_arg_expr', ''),
            'result_statuses': request.query_params.get('result_statuses', ''),
            'verdict': request.query_params.get('verdict', ''),
            'verdict_lookup': request.query_params.get('verdict_lookup', ''),
            'verdict_expr': request.query_params.get('verdict_expr', ''),
            'result_types': request.query_params.get('result_types', ''),
        }

    def _get_history(self, params):
        return HistoryService.get_history(**params)

    def _get_history_grouped(self, params):
        return HistoryService.get_history_grouped(**params)

    def list(self, request, pk=None):
        return self._get_cached_response(request, self._get_history)

    @action(detail=False, methods=['get'])
    def grouped(self, request, pk=None):
        return self._get_cached_response(request, self._get_history_grouped)

    def _get_cached_response(self, request, service_func):
        hashkey = generate_hashkey(request)
        response_data = cache.get(hashkey)

        if response_data is not None:
            return Response(response_data)

        params = self._extract_query_params(request)
        response_data = service_func(params)
        cache.set(hashkey, response_data)

        if hasattr(self, 'add_context') and self.add_context:
            response_data.update(self.add_context)

        return Response(response_data)
