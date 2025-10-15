# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.cache import RunCache
from bublik.core.filter_backends import ProjectFilterBackend
from bublik.core.measurement.services import (
    get_measurement_charts,
    get_measurement_results,
)
from bublik.core.queries import get_or_none
from bublik.core.run.compromised import (
    is_run_compromised,
    mark_run_compromised,
    unmark_run_compromised,
    validate_compromised_request,
)
from bublik.core.run.external_links import get_sources
from bublik.core.run.filter_expression import filter_by_expression
from bublik.core.run.stats import (
    generate_all_run_details,
    generate_result_details,
    generate_results_details,
    generate_runs_details,
    get_nok_results_distribution,
    get_run_stats_detailed_with_comments,
    get_run_status,
)
from bublik.core.run.tests_organization import get_test_ids_by_name
from bublik.core.shortcuts import serialize
from bublik.core.utils import get_difference
from bublik.data import models
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
    filter_backends: ClassVar[list] = [ProjectFilterBackend]

    def get_queryset(self):
        queryset = self.filter_queryset(
            models.TestIterationResult.objects.filter(test_run__isnull=True),
        )

        meta_types = ['tag', 'label', 'revision', 'branch']

        start_date = self.request.query_params.get('start_date')
        finish_date = self.request.query_params.get('finish_date')
        run_status = self.request.query_params.get('run_status')
        run_data = self.request.query_params.get('run_data')
        tag_expr = {'type': 'tag', 'expr': self.request.query_params.get('tag_expr', '')}
        label_expr = {'type': 'label', 'expr': self.request.query_params.get('label_expr', '')}
        revision_expr = {
            'type': 'revision',
            'expr': self.request.query_params.get('revision_expr', ''),
        }
        branch_expr = {
            'type': 'branch',
            'expr': self.request.query_params.get('branch_expr', ''),
        }

        if start_date:
            queryset = queryset.filter(start__date__gte=start_date)

        if finish_date:
            queryset = queryset.filter(finish__date__lte=finish_date)

        if run_status:
            project_id = self.request.query_params.get('project', None)
            queryset = queryset.filter_by_run_status(run_status, project_id)

        # Filter by tags, metadata passed as multiple values
        if run_data:
            run_data = run_data.split(settings.QUERY_DELIMITER)
            queryset = queryset.filter_by_run_metas(metas=run_data, meta_types=meta_types)

        # Filter tags, metadata by expression
        for meta_expr in [tag_expr, label_expr, revision_expr, branch_expr]:
            if meta_expr['expr']:
                queryset = filter_by_expression(
                    filtered_qs=queryset,
                    expr_str=meta_expr['expr'],
                    expr_type=meta_expr['type'],
                )

        return queryset.select_related('project').order_by('-start')

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
        return Response(data={'results': runs_ids}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def nok_distribution(self, request, pk=None):
        run = self.get_object()
        return Response(data=get_nok_results_distribution(run), status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def details(self, request, pk=None):
        run = self.get_object()
        return Response(data=generate_all_run_details(run), status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def stats(self, request, pk=None):
        requirements = self.request.query_params.get('requirements')
        run = self.get_object()
        return Response({'results': get_run_stats_detailed_with_comments(run.id, requirements)})

    @action(detail=True, methods=['get'])
    def requirements(self, request, pk=None):
        run = self.get_object()
        run_requirements = sorted(
            models.Meta.objects.filter(type='requirement', metaresult__result__test_run=run)
            .values_list('value', flat=True)
            .distinct(),
        )
        return Response({'requirements': run_requirements})

    @action(detail=True, methods=['get'])
    def source(self, request, pk=None):
        run = self.get_object()
        return Response({'url': get_sources(run)})

    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        run = self.get_object()
        return Response({'status': get_run_status(run)})

    @action(detail=True, methods=['get', 'post', 'delete'])
    def compromised(self, request, pk=None):
        run = self.get_object()
        if request.method == 'GET':
            compromised_data = is_run_compromised(run)
            if not compromised_data:
                compromised_data = {'compromised': False}
            return Response(compromised_data)
        if request.method == 'POST':
            comment = request.data.get('comment')
            bug_id = request.data.get('bug_id')
            reference_key = request.data.get('reference_key')

            try:
                err_msg = validate_compromised_request(run.id, comment, bug_id, reference_key)

                if not err_msg:
                    # TODO: This action should be reflected in the internal logger.
                    mark_run_compromised(run.id, comment, bug_id, reference_key)

            except Exception:
                # TODO: Add an exception details to the internal logger.
                err_msg = 'Unexpected internal error'

            if err_msg:
                raise ValidationError(err_msg)
            return Response(
                {'comment': comment, 'bug': f'Bug ID: {bug_id}' if bug_id else None},
            )
        try:
            unmark_run_compromised(run.id)
        except Exception as e:
            raise ValidationError(e) from ValidationError
        return Response({'message': f'Run {run.id} now is not compromised'})

    @action(
        detail=True,
        methods=['get', 'post', 'put', 'delete'],
        serializer_class=RunCommentSerializer,
    )
    def comment(self, request, pk=None):
        run = self.get_object()

        def get_comment(run):
            comments = models.MetaResult.objects.filter(meta__type='comment', result=run)
            if not comments.exists():
                return None
            if comments.count() > 1:
                msg = 'Multiple comments found for the run'
                raise ValidationError(msg)
            return comments.first()

        if request.method == 'GET':
            comment = get_comment(run)
            return Response({'comment': comment.meta.value if comment else None})

        if request.method == 'POST':
            mr_serializer = serialize(
                self.serializer_class,
                data={'comment': request.data.get('comment')},
                context={'run': run},
            )
            mr, _ = mr_serializer.get_or_create()
            return Response(
                self.serializer_class(mr).data,
                status=status.HTTP_201_CREATED,
            )

        if request.method == 'PUT':
            comment = get_comment(run)
            with transaction.atomic():
                comment.delete()
                mr_serializer = serialize(
                    self.serializer_class,
                    data={'comment': request.data.get('comment')},
                    context={'run': run},
                )
                mr, _ = mr_serializer.get_or_create()
            return Response(
                self.serializer_class(mr).data,
                status=status.HTTP_200_OK,
            )

        if request.method == 'DELETE':
            comment = get_comment(run)
            comment.delete()
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
            status=status.HTTP_200_OK,
        )

    def list(self, request):
        return Response(
            data={'results': generate_results_details(self.get_queryset())},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=['get'])
    def artifacts_and_verdicts(self, request, pk=None):
        result_metas = models.Meta.objects.filter(metaresult__result__id=pk)
        data = {
            'artifacts': list(result_metas.filter(type='artifact').values()),
            'verdicts': list(result_metas.filter(type='verdict').values()),
        }
        return Response(data, status=status.HTTP_200_OK)

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
