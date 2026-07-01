# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.result import ResultService
from bublik.core.run.stats import (
    generate_results_details,
)
from bublik.data.serializers import TestIterationResultSerializer
from bublik.interfaces.api_v2.result.schemas import result_viewset_schema


__all__ = [
    'ResultViewSet',
]


@result_viewset_schema
class ResultViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer
    filter_backends: ClassVar[list] = []

    def get_queryset(self):
        parent_id = self.request.query_params.get('parent_id')
        test_name = self.request.query_params.get('test_name')
        start_exec_seqno = self.request.query_params.get('start_exec_seqno')
        results = self.request.query_params.get('results')
        result_properties = self.request.query_params.get('result_properties')
        requirements = self.request.query_params.get('requirements')

        return ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
            start_exec_seqno=start_exec_seqno,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
        )

    def retrieve(self, request, pk=None):
        return Response(data={'result': ResultService.get_result_details(pk)})

    def list(self, request):
        return Response(
            data={'results': generate_results_details(self.get_queryset())},
        )

    @action(detail=True, methods=['get'])
    def artifacts_and_verdicts(self, request, pk=None):
        return Response(ResultService.get_result_artifacts_and_verdicts(pk))

    @action(detail=True, methods=['get'])
    def measurements(self, request, pk=None):
        return Response(ResultService.get_result_measurements(pk))
