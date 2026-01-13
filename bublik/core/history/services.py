# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Exists, F, OuterRef, Q

from bublik.core.datetime_formatting import display_to_date_in_numbers
from bublik.core.history.v2.utils import (
    group_results,
    group_results_by_iteration,
    prepare_list_results,
)
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.run.data import (
    get_metadata_by_runs,
    get_parameters_by_iterations,
    get_results,
    get_tags_by_runs,
    get_verdicts,
)
from bublik.core.run.filter_expression import filter_by_expression
from bublik.core.run.tests_organization import get_test_ids_by_name
from bublik.core.run.utils import prepare_dates_period
from bublik.data.models import (
    MeasurementResult,
    Meta,
    MetaResult,
    TestArgument,
    TestIteration,
    TestIterationResult,
)


class HistoryService:

    @staticmethod
    def build_history_queryset(
        test_name: str,
        project_id: int | None = None,
        run_ids: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        branches: str | None = None,
        revisions: str | None = None,
        labels: str | None = None,
        tags: str | None = None,
        branch_expr: str | None = None,
        rev_expr: str | None = None,
        label_expr: str | None = None,
        tag_expr: str | None = None,
        run_properties: str | None = None,
        hash: str | None = None,
        test_args: str | None = None,
        test_arg_expr: str | None = None,
        result_statuses: str | None = None,
        verdict: str | None = None,
        verdict_lookup: str | None = None,
        verdict_expr: str | None = None,
        result_types: str | None = None,
    ):
        """Build history queryset with all filters applied.

        Args:
            test_name: Name of the test
            project_id: Optional project filter
            run_ids: Comma-separated run IDs
            from_date: Start date (yyyy-mm-dd)
            to_date: End date (yyyy-mm-dd)
            branches: Comma-separated branch names
            revisions: Comma-separated revisions
            labels: Comma-separated labels
            tags: Comma-separated tags
            branch_expr: Branch filter expression
            rev_expr: Revision filter expression
            label_expr: Label filter expression
            tag_expr: Tag filter expression
            run_properties: Comma-separated run properties (e.g., 'compromised')
            hash: Iteration hash
            test_args: Comma-separated test arguments (key:value format)
            test_arg_expr: Test argument filter expression
            result_statuses: Comma-separated result statuses
            verdict: Verdict filter value
            verdict_lookup: Verdict lookup type ('regex', 'string', 'none')
            verdict_expr: Verdict filter expression
            result_types: Comma-separated result types ('expected', 'unexpected')

        Returns:
            Tuple of (queryset, from_date_obj, to_date_obj)

        Raises:
            ValidationError: if test name is invalid or no results found
        """
        query_delimiter = settings.QUERY_DELIMITER
        test_arg_delimiter = settings.KEY_VALUE_DELIMITER

        # Check test name
        test_ids = get_test_ids_by_name(test_name)
        if not test_ids:
            msg = f'Invalid test name: {test_name}'
            raise ValidationError(msg)

        ### Apply run filters ###

        # Filter by project and dates
        from_date_obj, to_date_obj, _ = prepare_dates_period(from_date, to_date, 30)
        runs_results = TestIterationResult.objects.filter(
            test_run__isnull=True,
            start__date__gte=from_date_obj,
            start__date__lte=to_date_obj,
        )

        # Apply project filter if provided
        if project_id:
            runs_results = runs_results.filter(project_id=project_id)

        # Filter by run IDs
        if run_ids:
            run_id_list = run_ids.split(query_delimiter)
            runs_results = runs_results.filter(id__in=run_id_list)

        # Combine branches, revisions, labels, tags
        run_metas = []
        for metas_string in [branches, revisions, labels, tags]:
            if metas_string:
                metas = metas_string.split(query_delimiter)
                run_metas.extend(filter(None, metas))

        # Filter by run metas
        if run_metas:
            runs_results = runs_results.filter_by_run_metas(run_metas)

        # Filter by run metas expressions
        for meta_type, expr in [
            ('branch', branch_expr),
            ('revision', rev_expr),
            ('label', label_expr),
            ('tag', tag_expr),
        ]:
            if expr:
                runs_results = filter_by_expression(
                    filtered_qs=runs_results,
                    expr_str=expr,
                    expr_type=meta_type,
                )

        # Filter by run properties
        if run_properties:
            runs_results = runs_results.filter_by_run_classification(
                run_properties.split(query_delimiter),
            )

        # Return empty if no runs found
        if not runs_results.exists():
            return TestIterationResult.objects.none(), from_date_obj, to_date_obj

        # Filter test results by found runs
        runs_results_ids = list(runs_results.values_list('id', flat=True))
        test_results = TestIterationResult.objects.filter(test_run__in=runs_results_ids)

        ### Apply iteration filters ###

        # Filter by test name
        test_iterations = TestIteration.objects.filter(test__in=test_ids)

        # Filter by hash
        if hash:
            test_iterations = test_iterations.filter(hash=hash)
            if not test_iterations.exists():
                msg = f'Invalid hash: {hash}'
                raise ValidationError(msg)
        else:
            test_iterations = test_iterations.filter(hash__isnull=False)

        # Filter by test arguments
        if test_args:
            test_arg_query = Q()
            for test_arg in filter(
                None,
                (arg.strip() for arg in test_args.split(query_delimiter)),
            ):
                parts = test_arg.split(test_arg_delimiter, 1)
                if len(parts) == 2:
                    arg_key, arg_value = (part.strip() for part in parts)
                    test_arg_query |= Q(name=arg_key, value=arg_value)

            test_args_filter = list(TestArgument.objects.filter(test_arg_query))
            if not test_args_filter:
                return test_results.none(), from_date_obj, to_date_obj

            for arg in test_args_filter:
                test_iterations = test_iterations.filter(test_arguments=arg)

        # Filter by test argument expression
        if test_arg_expr:
            test_iterations = filter_by_expression(
                filtered_qs=test_iterations,
                expr_str=test_arg_expr,
                expr_type='test_argument',
            )

        # Filter results by iterations
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
                verdict_lookup_str = '__iregex'
            elif verdict_lookup == 'string':
                verdict = verdict.split(query_delimiter)
                verdict_lookup_str = '__in'
            else:
                verdict_lookup_str = ''
            verdict_meta_filter = {'type': 'verdict'}
            verdict_meta_filter['value' + verdict_lookup_str] = verdict
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

        # Filter by result types
        if result_types:
            test_results = test_results.filter_by_result_classification(
                result_types.split(query_delimiter),
            )

        # Finish annotation
        return (
            test_results.select_related('test_run', 'iteration')
            .annotate(
                run_id=F('test_run__id'),
                iteration_hash=F('iteration__hash'),
                has_error=Exists(
                    MetaResult.objects.filter(result__id=OuterRef('id'), meta__type='err'),
                ),
                is_measurements=Exists(
                    MeasurementResult.objects.filter(result__id=OuterRef('id')),
                ),
            )
            .order_by('-start', 'id')
            .distinct('start', 'id')
            .values(
                'id',
                'start',
                'finish',
                'iteration_id',
                'iteration_hash',
                'run_id',
                'has_error',
                'is_measurements',
            ),
            from_date_obj,
            to_date_obj,
        )

    @staticmethod
    def prepare_results_data(test_results):
        '''Prepare results data for response.

        Args:
            test_results: Queryset of test results

        Returns:
            Tuple of (data dict, counts dict, runs_ids, iterations_ids, results_ids)
        '''
        # Collect IDs
        runs_ids = set()
        iterations_ids = set()
        results_ids = set()

        test_results_list = list(test_results)
        for result in test_results_list:
            runs_ids.add(result['run_id'])
            iterations_ids.add(result['iteration_id'])
            results_ids.add(result['id'])

        # Calculate counts
        total_results = len(test_results_list)
        unexpected_results = sum([result['has_error'] is True for result in test_results_list])

        counts = {
            'runs': len(runs_ids),
            'iterations': len(iterations_ids),
            'total_results': total_results,
            'expected_results': total_results - unexpected_results,
            'unexpected_results': unexpected_results,
        }

        # Get related data
        important_tags, relevant_tags = get_tags_by_runs(runs_ids)
        data = {
            'test_results': test_results_list,
            'results': get_results(results_ids),
            'verdicts': get_verdicts(results_ids),
            'parameters_by_iterations': get_parameters_by_iterations(iterations_ids),
            'metadata_by_runs': get_metadata_by_runs(runs_ids),
            'important_tags': important_tags,
            'relevant_tags': relevant_tags,
        }

        return data, counts, runs_ids, iterations_ids, results_ids

    @staticmethod
    def get_history(
        test_name: str,
        page: int | None = None,
        page_size: int | None = None,
        **filters,
    ) -> dict:
        '''Get test history (linear format).

        Args:
            test_name: Name of the test
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)
            **filters: Filter parameters (see build_history_queryset for details)

        Returns:
            Dictionary with history data, counts, and pagination info

        Raises:
            ValidationError: if invalid parameters
        '''

        # Build queryset
        test_results, from_date_obj, to_date_obj = HistoryService.build_history_queryset(
            test_name,
            **filters,
        )

        # Prepare data
        data, counts, _runs_ids, _iterations_ids, results_ids = (
            HistoryService.prepare_results_data(test_results)
        )

        # Apply pagination to test_results
        paginated_data = PaginatedResult.paginate_queryset(
            data['test_results'],
            page,
            page_size,
        )
        data['test_results'] = paginated_data['results']

        # Aggregate results
        response_list = prepare_list_results(
            data['test_results'],
            data['important_tags'],
            data['relevant_tags'],
            data['metadata_by_runs'],
            data['parameters_by_iterations'],
            data['results'],
            data['verdicts'],
        )

        return {
            'from_date': display_to_date_in_numbers(from_date_obj),
            'to_date': display_to_date_in_numbers(to_date_obj),
            'counts': counts,
            'pagination': paginated_data['pagination'],
            'results': response_list,
            'results_ids': list(results_ids),
        }

    @staticmethod
    def get_history_grouped(
        test_name: str,
        page: int | None = None,
        page_size: int | None = None,
        **filters,
    ) -> dict:
        '''Get test history grouped by iteration.

        Args:
            test_name: Name of the test
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)
            **filters: Filter parameters (see build_history_queryset for details)

        Returns:
            Dictionary with grouped history data, counts, and pagination info

        Raises:
            ValidationError: if invalid parameters
        '''

        # Build queryset
        test_results, from_date_obj, to_date_obj = HistoryService.build_history_queryset(
            test_name,
            **filters,
        )

        # Prepare data
        data, counts, _runs_ids, _iterations_ids, results_ids = (
            HistoryService.prepare_results_data(test_results)
        )

        # Group by iteration
        test_results_by_iteration = group_results_by_iteration(data['test_results'])
        grouped_results = []
        for _iteration_hash, iteration_results in test_results_by_iteration:
            grouped_results.append(list(iteration_results))

        # Apply pagination to grouped results
        paginated_data = PaginatedResult.paginate_queryset(grouped_results, page, page_size)

        # Aggregate results
        response_list = group_results(
            paginated_data['results'],
            data['important_tags'],
            data['relevant_tags'],
            data['parameters_by_iterations'],
            data['results'],
            data['verdicts'],
        )

        return {
            'from_date': display_to_date_in_numbers(from_date_obj),
            'to_date': display_to_date_in_numbers(to_date_obj),
            'counts': counts,
            'pagination': paginated_data['pagination'],
            'results': response_list,
            'results_ids': list(results_ids),
        }
