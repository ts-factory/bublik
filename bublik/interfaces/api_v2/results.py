# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.cache import RunCache
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
            msg = f'Unknown data key(s): {diff}'
            raise ValidationError(msg)
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


class ResultViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer
    filter_backends: ClassVar[list] = []

    def get_queryset(self):
        queries = Q()
        queryset = models.TestIterationResult.objects.filter()
        query_delimiter = settings.QUERY_DELIMITER
        errors = []

        parent_id = self.request.query_params.get('parent_id')
        test_name = self.request.query_params.get('test_name')
        results = self.request.query_params.get('results')
        result_properties = self.request.query_params.get('result_properties')
        requirements = self.request.query_params.get('requirements')

        if parent_id:
            if not get_or_none(models.TestIterationResult.objects, id=parent_id):
                errors.append('No test iteration result found by the given parent id')
            queries &= Q(parent_package=parent_id)

        if test_name:
            test_ids = get_test_ids_by_name(test_name)
            if not test_ids:
                errors.append('No tests found by the given test name')
            queries &= Q(iteration__test__in=test_ids, iteration__hash__isnull=False)

        if results:
            results = results.split(query_delimiter)
            diff = get_difference(results, models.ResultStatus.all_statuses())
            if diff:
                errors.append(f'Unknown result results: {diff}')
            queries &= Q(
                meta_results__meta__type='result',
                meta_results__meta__value__in=results,
            )

        if errors:
            raise ValidationError(errors)

        queryset = queryset.filter(queries)

        if result_properties:
            queryset = queryset.filter_by_result_classification(
                result_properties.split(query_delimiter),
            )

        if requirements:
            requirements = requirements.split(query_delimiter)
            available_req_metas = []
            for requirement in requirements:
                try:
                    available_req_metas.append(
                        models.Meta.objects.get(type='requirement', value=requirement),
                    )
                except models.Meta.DoesNotExist:
                    return models.TestIterationResult.objects.none()
            for req_meta in available_req_metas:
                queryset = queryset.filter(meta_results__meta=req_meta)

        return (
            queryset.order_by('-start', 'id')
            .select_related('iteration', 'project')
            .prefetch_related(
                'expectations',
                'expectations__expectmeta_set',
                'measurement_results',
                'meta_results__meta',
                'iteration__test_arguments',
            )
            .distinct('id', 'start')
        )

    def retrieve(self, request, pk=None):
        result = self.get_object()
        return Response(
            data={'result': generate_result_details(result)},
        )

    def list(self, request):
        return Response(
            data={'results': generate_results_details(self.get_queryset())},
        )

    @action(detail=True, methods=['get'])
    def artifacts_and_verdicts(self, request, pk=None):
        result_metas = models.Meta.objects.filter(metaresult__result__id=pk)
        data = {
            'artifacts': list(result_metas.filter(type='artifact').values()),
            'verdicts': list(result_metas.filter(type='verdict').values()),
        }
        return Response(data)

    @action(detail=True, methods=['get'])
    def measurements(self, request, pk=None):
        # get tables
        mmrs = get_measurement_results([pk])
        tables = [mmr.representation(additional='measurement') for mmr in mmrs]

        test_iter_res = self.get_object()
        data = {
            'run_id': test_iter_res.test_run_id,
            'iteration_id': test_iter_res.iteration_id,
            'charts': get_measurement_charts(pk),
            'tables': tables,
        }

        return Response(data)
