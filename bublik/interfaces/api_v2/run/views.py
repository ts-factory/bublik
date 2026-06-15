# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.cache import RunCache
from bublik.core.config.services import ConfigServices
from bublik.core.run.services import RunsChartGroupBy, RunService
from bublik.core.run.stats import (
    generate_runs_details,
)
from bublik.core.utils import get_difference
from bublik.data.models import GlobalConfigs, TestIterationResult
from bublik.data.serializers import (
    RunCommentSerializer,
    TestIterationResultSerializer,
)


__all__ = [
    'RunViewSet',
]


class RunViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer

    def get_queryset(self):
        query_params = self.request.query_params
        project_id = query_params.get('project')
        project_id = int(project_id) if project_id is not None else None

        return RunService.list_runs_queryset(
            start_date=query_params.get('start_date'),
            finish_date=query_params.get('finish_date'),
            project_id=project_id,
            run_status=query_params.get('run_status'),
            run_metas=query_params.get('run_metas'),
            tag_expr=query_params.get('tag_expr'),
            label_expr=query_params.get('label_expr'),
            revision_expr=query_params.get('revision_expr'),
            branch_expr=query_params.get('branch_expr'),
        )

    def list(self, request):
        results = self.paginate_queryset(self.get_queryset())
        return Response(
            {
                'pagination': self.paginator.get_pagination(),
                'results': generate_runs_details(results),
            },
        )

    @action(detail=False, methods=['get'])
    def charts(self, request):
        group_by_value = request.query_params.get('group_by', RunsChartGroupBy.DAY.value)

        try:
            group_by = RunsChartGroupBy(group_by_value)
        except ValueError as e:
            err_msg = f'group_by must be one of: {", ".join(RunsChartGroupBy.values())}'
            raise ValidationError(err_msg) from e

        return Response(
            RunService.aggregate_runs_by_period(self.get_queryset(), group_by=group_by),
        )

    @action(detail=False, methods=['post'])
    def drop_cache(self, request):
        keys = request.data.get('keys')
        diff = get_difference(keys, RunCache.KEY_DATA_CHOICES)
        if diff:
            msg = f'Unknown data key(s): {diff}'
            raise ValidationError(msg)
        kwargs = {'data_keys': keys}
        runs = self.get_queryset()
        runs_ids = list(runs.values_list('id', flat=True))
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
        project_id = TestIterationResult.objects.get(id=pk).project.id
        return Response(
            {
                'results': RunService.get_run_stats(pk, requirements),
                'default_columns': ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'RUN_STATS_COLUMNS_DEFAULT',
                    project_id,
                ),
            },
        )

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
            RunService.unmark_run_compromised(pk)
            return Response({'message': f'Run {pk} is no longer compromised'})
        except Exception as e:
            msg = 'Failed to unmark run due to internal error'
            raise ValidationError(msg) from e

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
