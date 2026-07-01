# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.result.serializers import (
    ResultArtifactsAndVerdictsResponseSerializer,
    ResultListQuerySerializer,
    ResultListResponseSerializer,
    ResultMeasurementsResponseSerializer,
    ResultRetrieveResponseSerializer,
)


RESULT_TAG = 'Results'


result_viewset_schema = extend_schema_view(
    retrieve=extend_schema(
        summary='Get result details',
        description="""
        Returns full details for a single test iteration result, including
        expected and obtained results, artifacts, parameters, comments,
        requirements, error state, and measurement availability.
        """,
        responses={
            200: OpenApiResponse(
                response=ResultRetrieveResponseSerializer,
                description='Result details were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[RESULT_TAG],
    ),
    list=extend_schema(
        summary='List results',
        description="""
        Returns test iteration results matching the provided parent, test name,
        execution sequence, result status, classification, and requirement
        filters.
        """,
        parameters=[ResultListQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=ResultListResponseSerializer,
                description='Results were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result filter validation failed',
            ),
        },
        tags=[RESULT_TAG],
    ),
    artifacts_and_verdicts=extend_schema(
        summary='Get result artifacts and verdicts',
        description="""
        Returns artifact and verdict meta values for a result.
        """,
        responses={
            200: OpenApiResponse(
                response=ResultArtifactsAndVerdictsResponseSerializer,
                description='Artifacts and verdicts were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[RESULT_TAG],
    ),
    measurements=extend_schema(
        summary='Get result measurements',
        description="""
        Returns measurement chart and table data for a result.
        """,
        responses={
            200: OpenApiResponse(
                response=ResultMeasurementsResponseSerializer,
                description='Result measurements were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[RESULT_TAG],
    ),
)
