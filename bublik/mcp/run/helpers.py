# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.


from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q
from rest_framework.exceptions import ValidationError

from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.result import ResultService
from bublik.core.run.services import RunService
from bublik.core.run.stats import generate_results_details
from bublik.data import models

from .models import RunLeafResultsPayload


if TYPE_CHECKING:
    from bublik.core.run.dto import RunStatsResult


def _find_stats_node(node: RunStatsResult, result_id: int) -> RunStatsResult | None:
    if node.result_id == result_id:
        return node

    for child in node.children:
        found = _find_stats_node(child, result_id)
        if found is not None:
            return found

    return None


def _list_unexpected_or_abnormal_results(
    *,
    parent_id: int,
    test_name: str,
    start_exec_seqno: int,
    requirements: str | None,
    page: int | None,
    page_size: int | None,
) -> dict:
    queryset = ResultService.list_results(
        parent_id=parent_id,
        test_name=test_name,
        start_exec_seqno=start_exec_seqno,
        requirements=requirements,
    )
    queryset = queryset.filter(
        Q(meta_results__meta__type='err') | Q(meta_results__meta__in=models.Meta.abnormal),
    )
    result_details = generate_results_details(queryset)
    return PaginatedResult.paginate_queryset(result_details, page, page_size)


def _get_run_leaf_results(
    leaf_result_id: int,
    requirements: str | None = None,
    results: str | None = None,
    result_properties: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    unexpected_only: bool = False,
) -> RunLeafResultsPayload:
    if unexpected_only and any((results, result_properties)):
        msg = 'unexpected_only cannot be combined with results or result_properties'
        raise ValidationError(msg)

    result = ResultService.get_result(leaf_result_id)
    run_id = result.test_run_id
    if run_id is None:
        msg = 'A test leaf result ID from get_run_overview is required'
        raise ValidationError(msg)

    stats = RunService.get_run_stats(run_id, None)
    if stats is None:
        msg = f'Run statistics are unavailable for run {run_id}'
        raise ValidationError(msg)

    leaf = _find_stats_node(stats, leaf_result_id)
    if leaf is None:
        msg = (
            'Result ID is not an aggregate leaf ID; use the leaf result ID '
            'shown by get_run_overview'
        )
        raise ValidationError(msg)
    if (
        leaf.type != 'test'
        or leaf.children
        or leaf.parent_id is None
        or leaf.exec_seqno is None
    ):
        msg = 'A test leaf result ID from get_run_overview is required'
        raise ValidationError(msg)

    if unexpected_only:
        paginated = _list_unexpected_or_abnormal_results(
            parent_id=leaf.parent_id,
            test_name=leaf.test_name,
            start_exec_seqno=leaf.exec_seqno,
            requirements=requirements,
            page=page,
            page_size=page_size,
        )
    else:
        paginated = ResultService.list_results_paginated(
            parent_id=leaf.parent_id,
            test_name=leaf.test_name,
            start_exec_seqno=leaf.exec_seqno,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
            page=page,
            page_size=page_size,
        )
    result_rows = [
        {
            **row,
            'classification': 'unexpected' if row['has_error'] else 'expected',
        }
        for row in paginated['results']
    ]

    return RunLeafResultsPayload.model_validate(
        {
            'leaf': {
                'result_id': leaf.result_id,
                'run_id': run_id,
                'test_name': leaf.test_name,
                'path': leaf.path,
            },
            'requirements': requirements,
            'pagination': paginated['pagination'],
            'results': result_rows,
        },
    )
