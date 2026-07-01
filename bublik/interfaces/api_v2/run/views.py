# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

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
from bublik.data.models import GlobalConfigs
from bublik.data.serializers import (
    TestIterationResultSerializer,
)
from bublik.interfaces.api_v2.run.schemas import (
    create_comment_schema,
    delete_comment_schema,
    mark_compromised_schema,
    run_viewset_schema,
    unmark_compromised_schema,
    update_comment_schema,
)
from bublik.interfaces.api_v2.run.serializers import (
    RunCommentRequestSerializer,
    serialize_mark_run_compromised_result,
    serialize_run_comment_result,
    serialize_run_details,
    serialize_run_stats_result,
    serialize_run_summary_results,
)


__all__ = [
    'RunViewSet',
]


@run_viewset_schema
class RunViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer
    filter_backends: ClassVar[list] = []

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
                'results': serialize_run_summary_results(generate_runs_details(results)),
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
        return Response(data=serialize_run_details(RunService.get_run_details(pk)))

    @action(detail=True, methods=['get'])
    def stats(self, _request, pk=None):
        requirements = self.request.query_params.get('requirements')
        run = RunService.get_run(pk)
        project_id = run.project.id
        run_stats = RunService.get_run_stats(pk, requirements)
        return Response(
            {
                'results': serialize_run_stats_result(run_stats),
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

    @action(detail=True, methods=['get'])
    def compromised(self, _request, pk=None):
        return Response(RunService.get_run_compromised(pk))

    @mark_compromised_schema
    @compromised.mapping.post
    def mark_compromised(self, request, pk=None):
        comment = request.data.get('comment')
        bug_id = request.data.get('bug_id')
        reference_key = request.data.get('reference_key')
        return Response(
            serialize_mark_run_compromised_result(
                RunService.mark_run_compromised(pk, comment, bug_id, reference_key),
            ),
        )

    @unmark_compromised_schema
    @compromised.mapping.delete
    def unmark_compromised(self, _request, pk=None):
        try:
            RunService.unmark_run_compromised(pk)
            return Response({'message': f'Run {pk} is no longer compromised'})
        except Exception as e:
            msg = 'Failed to unmark run due to internal error'
            raise ValidationError(msg) from e

    @action(
        detail=True,
        methods=['get'],
        serializer_class=RunCommentRequestSerializer,
    )
    def comment(self, _request, pk=None):
        comment = RunService.get_run_comment(pk)
        return Response({'comment': comment})

    @create_comment_schema
    @comment.mapping.post
    def create_comment(self, request, pk=None):
        content = request.data.get('comment')
        result = RunService.create_run_comment(pk, content)
        return Response(
            serialize_run_comment_result(result),
            status=status.HTTP_201_CREATED,
        )

    @update_comment_schema
    @comment.mapping.put
    def update_comment(self, request, pk=None):
        content = request.data.get('comment')
        result = RunService.create_run_comment(pk, content)
        return Response(serialize_run_comment_result(result), status=status.HTTP_200_OK)

    @delete_comment_schema
    @comment.mapping.delete
    def delete_comment(self, _request, pk=None):
        RunService.delete_run_comment(pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
