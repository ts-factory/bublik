# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.history.serializers import (
    HistoryGroupedResponseSerializer,
    HistoryListQuerySerializer,
    HistoryListResponseSerializer,
    HistoryMetasSearchOptionsSerializer,
    HistoryParamsSearchQuerySerializer,
    HistoryProjectQuerySerializer,
)


HISTORY_TAG = 'History'
STRING_LIST_SCHEMA = {'type': 'array', 'items': {'type': 'string'}}


history_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List test history',
        description="""
        Returns paginated results for a test, including expected and obtained
        outcomes, verdicts, tags, metadata, parameters, and result counts.
        """,
        parameters=[HistoryListQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=HistoryListResponseSerializer,
                description='Test history was successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Test name is missing or pagination parameters are invalid',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Test, iteration hash, or requested page was not found',
            ),
        },
        tags=[HISTORY_TAG],
    ),
    grouped=extend_schema(
        summary='List test history grouped by iteration',
        description="""
        Returns paginated test history grouped by iteration and verdict, with
        result counts, tags, parameters, and result identifiers.
        """,
        parameters=[HistoryListQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=HistoryGroupedResponseSerializer,
                description='Grouped test history was successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Test name is missing or pagination parameters are invalid',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Test, iteration hash, or requested page was not found',
            ),
        },
        tags=[HISTORY_TAG],
    ),
    test_search_options=extend_schema(
        summary='List test search options',
        description='Returns test names and paths available for history search.',
        parameters=[HistoryProjectQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=STRING_LIST_SCHEMA,
                description='Test search options were successfully retrieved',
            ),
        },
        tags=[HISTORY_TAG],
    ),
    params_search_options=extend_schema(
        summary='List test parameter search options',
        description='Returns parameter names and values for the selected test.',
        parameters=[HistoryParamsSearchQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=STRING_LIST_SCHEMA,
                description='Test parameter options were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Test name is missing',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Test was not found',
            ),
        },
        tags=[HISTORY_TAG],
    ),
    metas_search_options=extend_schema(
        summary='List metadata search options',
        description='Returns tags, branches, revisions, and labels for history search.',
        parameters=[HistoryProjectQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=HistoryMetasSearchOptionsSerializer,
                description='Metadata search options were successfully retrieved',
            ),
        },
        tags=[HISTORY_TAG],
    ),
)
