# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from dataclasses import asdict
from typing import ClassVar

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import check_action_permission_for_projects, get_request_user
from bublik.core.result import ResultService
from bublik.core.result.services import UNSET
from bublik.core.run.stats import (
    generate_results_details,
)
from bublik.data.serializers import (
    TestIterationResultSerializer,
)
from bublik.interfaces.api_v2.result.schemas import result_viewset_schema
from bublik.interfaces.api_v2.result.serializers import (
    ClassifyOptionsRequestSerializer,
    ClassifyOptionsResponseSerializer,
    ClassifyRequestSerializer,
    ClassifyResponseSerializer,
    UnclassifyRequestSerializer,
    UnclassifyResponseSerializer,
)


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
        issue = self.request.query_params.get('issue')

        return ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
            start_exec_seqno=start_exec_seqno,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
            issue=issue,
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

    @action(
        detail=False,
        methods=['post'],
        url_path='classify/options',
        serializer_class=ClassifyOptionsRequestSerializer,
    )
    def classify_options(self, request):
        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        result_ids = body_serializer.validated_data['result_ids']

        found = ResultService.get_results_by_ids(result_ids)
        options = ResultService.get_classify_options(
            ResultService.validate_results_for_classification(result_ids, found),
        )
        return Response(ClassifyOptionsResponseSerializer(options).data)

    @action(detail=False, methods=['post'], serializer_class=ClassifyRequestSerializer)
    def classify(self, request):
        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        body = body_serializer.validated_data
        result_ids = body['result_ids']

        found = ResultService.get_results_by_ids(result_ids)
        # Before anything is told about the results, as it depends on their projects.
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [result.project_id for result in found.values()],
        )
        results = ResultService.validate_results_for_classification(result_ids, found)
        outcome = ResultService.classify(
            results,
            issue=body.get('issue'),
            categories=body['categories'],
            matchers=body['matchers'],
            scope=body['scope'],
            expected=body.get('expected', UNSET),
            actor=get_request_user(request),
        )
        data = ClassifyResponseSerializer(asdict(outcome)).data
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], serializer_class=UnclassifyRequestSerializer)
    def unclassify(self, request):
        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        body = body_serializer.validated_data

        found = ResultService.get_rule_results_by_ids(body['rule_result_ids'])
        # Before anything is told about the stamps, as it depends on their projects.
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [rule_result.result.project_id for rule_result in found.values()],
        )

        outcome = ResultService.unclassify_results(body['rule_result_ids'], found)
        return Response(UnclassifyResponseSerializer(outcome).data)
