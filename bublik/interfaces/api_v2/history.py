# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import chain

from django.conf import settings
from django.core.cache import cache
from django.db.models import Exists, F, OuterRef, Q
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.datetime_formatting import display_to_date_in_numbers
from bublik.core.history.v2.utils import (
    generate_hashkey,
    group_results,
    group_results_by_iteration,
    prepare_list_results,
)
from bublik.core.run.data import (
    get_metadata_by_runs,
    get_parameters_by_iterations,
    get_results,
    get_tags_by_runs,
    get_verdicts,
)
from bublik.core.run.filter_expression import filter_by_expression
from bublik.core.run.tests_organization import get_tests_by_name
from bublik.core.run.utils import prepare_dates_period
from bublik.data.models import (
    MeasurementResult,
    Meta,
    MetaResult,
    TestArgument,
    TestIteration,
    TestIterationResult,
)


__all__ = [
    'HistoryViewSet',
]


class HistoryViewSet(ListModelMixin, GenericViewSet):
    filter_backends = []

    def get_queryset(self):
        query_delimiter = settings.QUERY_DELIMITER
        test_arg_delimiter = settings.KEY_VALUE_DELIMITER

        # Get test name
        test_name = self.request.query_params.get('test_name')

        ### Get run filter params ###
        # Get period
        from_date = self.request.query_params.get('from_date', '')
        to_date = self.request.query_params.get('to_date', '')
        # Get meta data
        branches = self.request.query_params.get('branches', '')
        revisions = self.request.query_params.get('revisions', '')
        labels = self.request.query_params.get('labels', '')
        tags = self.request.query_params.get('tags', '')
        # Get meta data expressions
        branch_expr = {
            'type': 'branch',
            'expr': self.request.query_params.get('branch_expr', ''),
        }
        rev_expr = {
            'type': 'revision',
            'expr': self.request.query_params.get('rev_expr', ''),
        }
        label_expr = {'type': 'label', 'expr': self.request.query_params.get('label_expr', '')}
        tag_expr = {'type': 'tag', 'expr': self.request.query_params.get('tag_expr', '')}
        # Get run properties: compromised / not compromised sessions
        run_properties = self.request.query_params.get('run_properties', '')

        ### Get iteration filter params ###
        # Get hash
        hash = self.request.query_params.get('hash', '')
        # Get test arguments and test argument expression
        test_args = self.request.query_params.get('test_args', [])
        test_arg_expr = self.request.query_params.get('test_arg_expr', '')

        ### Get result filter params ###
        # Get result statuses: PASSED / FAILED / KILLED / CORED / SKIPPED / FAKED / INCOMPLETE
        result_statuses = self.request.query_params.get('result_statuses', '')
        # Get verdict, its lookup and expression
        verdict = self.request.query_params.get('verdict', '')
        verdict_lookup = self.request.query_params.get('verdict_lookup', '')
        verdict_expr = self.request.query_params.get('verdict_expr', '')
        # Get result types: expected / unexpected results
        result_types = self.request.query_params.get('result_types', '')

        # Check passed test name first
        tests = get_tests_by_name(test_name)
        if not tests:
            raise ValidationError(detail='Invalid test name', code=status.HTTP_404_NOT_FOUND)

        ### Apply run filters ###

        # Filter by dates
        self.from_date, self.to_date, _ = prepare_dates_period(from_date, to_date, 30)
        runs_results = TestIterationResult.objects.filter(
            test_run__isnull=True,
            start__date__gte=self.from_date,
            start__date__lte=self.to_date,
        )

        # Combine branches, revisions, labels, tags to the one set of metas
        run_metas = []
        for metas_string in [branches, revisions, labels, tags]:
            metas = metas_string.split(query_delimiter)
            run_metas.extend(filter(None, metas))

        # Filter by run metas passed as multiple values
        if run_metas:
            runs_results = runs_results.filter_by_run_metas(run_metas)

        # Filter by run metas expressions
        for meta_expr in [branch_expr, rev_expr, label_expr, tag_expr]:
            if meta_expr['expr']:
                runs_results = filter_by_expression(
                filtered_qs=runs_results,
                expr_str=meta_expr['expr'],
                expr_type=meta_expr['type'],
            )

        # Filter by run property: compromised / not compromised sessions
        if run_properties:
            runs_results = runs_results.filter_by_run_classification(
                run_properties.split(query_delimiter),
            )

        # Return empty list if no runs found
        if not runs_results:
            return TestIterationResult.objects.none()

        # Filter test results by found runs
        runs_results_ids = list(runs_results.values_list('id', flat=True))
        test_results = TestIterationResult.objects.filter(test_run__in=runs_results_ids)

        ### Apply iteration filters ###

        # Filter test iterations by test name
        test_ids = list(tests.values_list('id', flat=True))
        test_iterations = TestIteration.objects.filter(test__in=test_ids)

        # Filter test iterations by hash
        if hash:
            test_iterations = test_iterations.filter(hash=hash)
            if not test_iterations.exists():
                raise ValidationError(detail='Invalid hash', code=status.HTTP_404_NOT_FOUND)
        else:
            test_iterations = test_iterations.filter(hash__isnull=False)

        # Prepare filter by test arguments
        test_args_filter = []
        if len(test_args) != 0:
            test_arg_query = Q()
            for test_arg in test_args.split(query_delimiter):
                arg_key, arg_value = test_arg.split(test_arg_delimiter, 1)
                test_arg_query |= Q(name=arg_key, value=arg_value)

            test_args_filter = list(TestArgument.objects.filter(test_arg_query))

        # Apply filter by test arguments
        if test_args_filter:
            for arg in test_args_filter:
                test_iterations = test_iterations.filter(test_arguments=arg)

        # Apply filter by test arguments expression
        if test_arg_expr:
            test_iterations = filter_by_expression(
                filtered_qs=test_iterations,
                expr_str=test_arg_expr,
                expr_type='test_argument',
            )

        # Filter test results by found iterations
        test_iteration_ids = list(test_iterations.values_list('id', flat=True))
        test_results = test_results.filter(iteration__in=test_iteration_ids)

        ### Apply result filters ###

        # Filter by result statuses
        if result_statuses:
            result_meta_ids = list(
                Meta.objects.filter(
                    type='result',
                    value__in=result_statuses.split(query_delimiter),
                ).values_list('id', flat=True),
            )
            test_results = test_results.filter(meta_results__meta__in=result_meta_ids)

        # Filter by verdicts
        if verdict:
            if verdict_lookup == 'regex':
                verdict_lookup = '__iregex'
            elif verdict_lookup == 'string':
                verdict = verdict.split(query_delimiter)
                verdict_lookup = '__in'
            else:
                verdict_lookup = ''
            verdict_meta_filter = {'type': 'verdict'}
            verdict_meta_filter['value' + verdict_lookup] = verdict
            verdict_meta_ids = list(
                Meta.objects.filter(**verdict_meta_filter).values_list('id', flat=True),
            )
            test_results = test_results.filter(meta_results__meta__in=verdict_meta_ids)
        elif not verdict and verdict_lookup == 'none':
            test_results = test_results.exclude(meta_results__meta__type='verdict')

        # Filter by verdict expression
        if verdict_expr:
            test_results = filter_by_expression(
                filtered_qs=test_results,
                expr_str=verdict_expr,
                expr_type='verdict',
            )

        # Filter by result type classification: expected / unexpected results
        if result_types:
            test_results = test_results.filter_by_result_classification(
                result_types.split(query_delimiter),
            )

        # Finish annotation
        test_results = test_results.select_related('test_run', 'iteration').annotate(
                run_id=F('test_run__id'),
                iteration_hash=F('iteration__hash'),
                has_error=Exists(
                    MetaResult.objects.filter(result__id=OuterRef('id'), meta__type='err'),
                ),
                is_measurements=Exists(
                    MeasurementResult.objects.filter(result__id=OuterRef('id')),
                ),
            ).order_by('-start', 'id').distinct('start', 'id').values(
                'id',
                'start',
                'finish',
                'iteration_id',
                'iteration_hash',
                'run_id',
                'has_error',
                'is_measurements',
            )

        return test_results

    def prepare_results_data(self, test_results, grouped=False):
        '''This is a hand-help method to prepare results data available as instance fields.'''

        # Prepare results, iterations and runs IDs
        self.runs_ids = set()
        self.iterations_ids = set()
        self.results_ids = set()

        for result in test_results:
            self.runs_ids.add(result['run_id'])
            self.iterations_ids.add(result['iteration_id'])
            self.results_ids.add(result['id'])

        # Calculate entities of the filtered results (!) before pagination
        total_results = len(test_results)
        unexpected_results = sum([result['has_error'] is True for result in test_results])

        counts = {
            'runs': len(self.runs_ids),
            'iterations': len(self.iterations_ids),
            'total_results': total_results,
            'expected_results': total_results - unexpected_results,
            'unexpected_results': unexpected_results,
        }

        data = {}
        # Apply pagination
        if grouped:
            test_results_by_iteration = group_results_by_iteration(test_results)
            lists_test_results = []
            for _iteration_hash, test_results in test_results_by_iteration:
                lists_test_results.append(list(test_results))

            test_results_by_iteration = self.paginate_queryset(lists_test_results)
            test_results = chain(*test_results_by_iteration)
            data['test_results'] = test_results_by_iteration
        else:
            test_results = self.paginate_queryset(test_results)
            data['test_results'] = test_results

        # Update results, iterations and runs IDs after pagination
        for result in test_results:
            self.runs_ids.add(result['run_id'])
            self.iterations_ids.add(result['iteration_id'])
            self.results_ids.add(result['id'])

        # Prepare results data by entities they belong
        important_tags, relevant_tags = get_tags_by_runs(self.runs_ids)
        data.update(
            {
                'results': get_results(self.results_ids),
                'verdicts': get_verdicts(self.results_ids),
                'parameters_by_iterations': get_parameters_by_iterations(self.iterations_ids),
                'metadata_by_runs': get_metadata_by_runs(self.runs_ids),
                'important_tags': important_tags,
                'relevant_tags': relevant_tags,
            },
        )

        return data, counts

    def prepare_response(self, response_list, counts, add_context=None):
        '''This is a hand-help method to collect all data for returning in Response object.'''

        if add_context is None:
            add_context = {}
        response = {
            'from_date': display_to_date_in_numbers(self.from_date),
            'to_date': display_to_date_in_numbers(self.to_date),
            'counts': counts,
            'pagination': self.paginator.get_pagination(),
            'results': response_list,
            'results_ids': self.results_ids,
        }

        if add_context:
            response.update(add_context)

        return response

    def list(self, request, pk=None):
        # Try to use response_list from the cache based on the request
        hashkey = generate_hashkey(request)
        response_data = cache.get(hashkey)
        if response_data is not None:
            return Response(response_data)

        # Filter results
        test_results = self.get_queryset()

        # Prepare results data available as instance fields
        data, counts = self.prepare_results_data(test_results)

        # Aggregate test results for response
        response_list = prepare_list_results(
            data['test_results'],
            data['important_tags'],
            data['relevant_tags'],
            data['metadata_by_runs'],
            data['parameters_by_iterations'],
            data['results'],
            data['verdicts'],
        )

        # Prepare response data
        response_data = self.prepare_response(response_list, counts)

        # Save response_list to cache with based on the request key
        cache.set(hashkey, response_data)

        return Response(response_data)

    @action(detail=False, methods=['get'])
    def grouped(self, request, pk=None):
        # Try to use response_list from the cache based on the request
        hashkey = generate_hashkey(request)
        response_data = cache.get(hashkey)
        if response_data is not None:
            return Response(response_data)

        # Get filtered results
        test_results = self.get_queryset()

        # Prepare results data available as instance fields
        data, counts = self.prepare_results_data(test_results, grouped=True)

        # Aggregate test results for response
        response_list = group_results(
            data['test_results'],
            data['important_tags'],
            data['relevant_tags'],
            data['parameters_by_iterations'],
            data['results'],
            data['verdicts'],
        )

        # Prepare response data
        response_data = self.prepare_response(response_list, counts)

        # Save response_list to cache with based on the request key
        cache.set(hashkey, response_data)

        return Response(response_data)
