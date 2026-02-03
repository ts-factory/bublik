# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.cache import RunCache
from bublik.core.result import ResultService
from bublik.core.run.services import RunService
from bublik.core.run.stats import (
    generate_results_details,
    generate_runs_details,
)
from bublik.core.utils import get_difference
from bublik.data.serializers import (
    RunCommentSerializer,
    TestIterationResultSerializer,
)


all = [
    'RunViewSet',
    'ResultViewSet',
]


class RunViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer

    def get_queryset(self):
        '''Delegate to RunService for consistency with MCP tools.'''
        return RunService.list_runs_queryset(
            start_date=self.request.query_params.get('start_date'),
            finish_date=self.request.query_params.get('finish_date'),
            project_id=self.request.query_params.get('project'),
            run_status=self.request.query_params.get('run_status'),
            run_data=self.request.query_params.get('run_data'),
            tag_expr=self.request.query_params.get('tag_expr'),
            label_expr=self.request.query_params.get('label_expr'),
            revision_expr=self.request.query_params.get('revision_expr'),
            branch_expr=self.request.query_params.get('branch_expr'),
        )

    def list(self, request):
        results = self.paginate_queryset(self.get_queryset())
        return Response(
            {
                'pagination': self.paginator.get_pagination(),
                'results': generate_runs_details(results),
            },
        )

    @action(detail=False, methods=['post'])
    def drop_cache(self, request):
        keys = request.data.get('keys')
        diff = get_difference(keys, RunCache.KEY_DATA_CHOICES)
        if diff:
            error_response = {'message': f'Unknown data key(s): {diff}'}
            return Response(data=error_response, status=status.HTTP_400_BAD_REQUEST)
        kwargs = {'data_keys': keys}
        runs = self.get_queryset()
        runs_ids = runs.values_list('id', flat=True)
        for run in runs:
            kwargs.update({'run': run})
            RunCache.delete_data_for_obj(**kwargs)
        return Response(data={'results': runs_ids})

    @action(detail=True, methods=['get'])
    def nok_distribution(self, _request, pk=None):
        return Response(data=RunService.get_nok_distribution(pk))

    @action(detail=True, methods=['get'])
    def details(self, _request, pk=None):
        return Response(data=RunService.get_run_details(pk))

    @action(detail=True, methods=['get'])
    def stats(self, _request, pk=None):
        requirements = self.request.query_params.get('requirements')
        return Response({'results': RunService.get_run_stats(pk, requirements)})

    @action(detail=True, methods=['get'])
    def requirements(self, _request, pk=None):
        return Response({'requirements': RunService.get_run_requirements(pk)})

    @action(detail=True, methods=['get'])
    def source(self, _request, pk=None):
        return Response({'url': RunService.get_run_source(pk)})

    @action(detail=True, methods=['get'])
    def status(self, _request, pk=None):
        return Response({'status': RunService.get_run_status(pk)})

    @action(detail=True, methods=['get', 'post', 'delete'])
    def compromised(self, request, pk=None):
        if request.method == 'GET':
            return Response(RunService.get_run_compromised(pk))
        if request.method == 'POST':
            comment = request.data.get('comment')
            bug_id = request.data.get('bug_id')
            reference_key = request.data.get('reference_key')
            return Response(RunService.mark_run_compromised(pk, comment, bug_id, reference_key))
        try:
            return Response(RunService.unmark_run_compromised(pk))
        except Exception as e:
            raise ValidationError(e) from ValidationError

    @action(
        detail=True,
        methods=['get', 'post', 'put', 'delete'],
        serializer_class=RunCommentSerializer,
    )
    def comment(self, request, pk=None):
        if request.method == 'GET':
            comment = RunService.get_run_comment(pk)
            return Response({'comment': comment})

        if request.method == 'POST':
            content = request.data.get('comment')
            result = RunService.create_run_comment(pk, content)
            return Response(result, status=status.HTTP_201_CREATED)

        if request.method == 'PUT':
            content = request.data.get('comment')
            result = RunService.create_run_comment(pk, content)
            return Response(result, status=status.HTTP_200_OK)

        if request.method == 'DELETE':
            RunService.delete_run_comment(pk)
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(status=status.HTTP_405_METHOD_NOT_ALLOWED)


class ResultViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer
    filter_backends: ClassVar[list] = []

    def get_queryset(self):
        '''Delegate to ResultService for consistency with MCP tools.'''
        parent_id = self.request.query_params.get('parent_id')
        test_name = self.request.query_params.get('test_name')
        results = self.request.query_params.get('results')
        result_properties = self.request.query_params.get('result_properties')
        requirements = self.request.query_params.get('requirements')

        return ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
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
