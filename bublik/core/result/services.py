# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q

from bublik.core.measurement.services import (
    get_measurement_charts,
    get_measurement_results,
)
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.queries import get_or_none
from bublik.core.run.stats import (
    generate_result_details,
    generate_results_details,
)
from bublik.core.run.tests_organization import get_test_ids_by_name
from bublik.core.utils import get_difference
from bublik.data import models


class ResultService:
    '''Service for result-related operations (shared between REST API and MCP).'''

    @staticmethod
    def get_result(result_id: int) -> models.TestIterationResult:
        '''Get a result by ID.

        Args:
            result_id: The ID of the test result

        Returns:
            TestIterationResult instance

        Raises:
            ValidationError: if result not found
        '''
        try:
            return models.TestIterationResult.objects.get(id=result_id)
        except models.TestIterationResult.DoesNotExist as e:
            msg = f'Result {result_id} not found'

            raise ValidationError(msg) from e

    @staticmethod
    def get_result_details(result_id: int) -> dict:
        '''Get full details for a single result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with full result details
        '''

        result = ResultService.get_result(result_id)

        return generate_result_details(result)

    @staticmethod
    def get_result_measurements(result_id: int) -> dict:
        '''Get measurements for a result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with run_id, iteration_id, charts, and tables
        '''
        result = ResultService.get_result(result_id)

        # Get tables
        mmrs = get_measurement_results([result_id])
        tables = [mmr.representation(additional='measurement') for mmr in mmrs]

        return {
            'run_id': result.test_run_id,
            'iteration_id': result.iteration_id,
            'charts': get_measurement_charts(result_id),
            'tables': tables,
        }

    @staticmethod
    def get_result_artifacts_and_verdicts(result_id: int) -> dict:
        '''Get artifacts and verdicts for a result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with artifacts and verdicts lists
        '''
        result_metas = models.Meta.objects.filter(metaresult__result__id=result_id)
        return {
            'artifacts': list(result_metas.filter(type='artifact').values()),
            'verdicts': list(result_metas.filter(type='verdict').values()),
        }

    @staticmethod
    def list_results(
        parent_id: int | None = None,
        test_name: str | None = None,
        results: str | None = None,
        result_properties: str | None = None,
        requirements: str | None = None,
    ):
        '''List results with filtering.

        This method provides a single source of truth for result filtering,
        shared between REST API and MCP tools.

        Args:
            parent_id: Filter by parent package ID
            test_name: Filter by test name
            results: Comma-separated result statuses
            result_properties: Comma-separated result properties
            requirements: Comma-separated requirement names

        Returns:
            QuerySet of filtered TestIterationResult objects

        Raises:
            ValidationError: if validation fails
        '''
        queries = Q()
        queryset = models.TestIterationResult.objects.filter()
        query_delimiter = settings.QUERY_DELIMITER
        errors = []

        # parent_id filtering
        if parent_id:
            if not get_or_none(models.TestIterationResult.objects, id=parent_id):
                errors.append('No test iteration result found by the given parent id')
            queries &= Q(parent_package=parent_id)

        # test_name filtering
        if test_name:
            test_ids = get_test_ids_by_name(test_name)
            if not test_ids:
                errors.append('No tests found by the given test name')
            queries &= Q(iteration__test__in=test_ids, iteration__hash__isnull=False)

        # results/status filtering
        if results:
            results_list = results.split(query_delimiter)
            diff = get_difference(results_list, models.ResultStatus.all_statuses())
            if diff:
                errors.append(f'Unknown result results: {diff}')
            queries &= Q(
                meta_results__meta__type='result',
                meta_results__meta__value__in=results_list,
            )

        if errors:
            raise ValidationError(errors)

        queryset = queryset.filter(queries)

        # result_properties filtering
        if result_properties:
            queryset = queryset.filter_by_result_classification(
                result_properties.split(query_delimiter),
            )

        # requirements filtering
        if requirements:
            requirements_list = requirements.split(query_delimiter)
            available_req_metas = []
            for requirement in requirements_list:
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

    @staticmethod
    def list_results_paginated(
        parent_id: int | None = None,
        test_name: str | None = None,
        results: str | None = None,
        result_properties: str | None = None,
        requirements: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        '''List results with filtering and pagination.

        Args:
            parent_id: Filter by parent package ID
            test_name: Filter by test name
            results: Comma-separated result statuses
            result_properties: Comma-separated result properties
            requirements: Comma-separated requirement names
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and result details
        '''
        queryset = ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
        )

        results_details = generate_results_details(queryset)
        return PaginatedResult.paginate_queryset(results_details, page, page_size)
