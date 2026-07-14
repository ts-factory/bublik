# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.result.serializers import (
    ClassifyRequestSerializer,
    ClassifyResponseSerializer,
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
    classify=extend_schema(
        summary='Classify a result',
        description="""
        Finds/creates an Issue, creates an IssueRule (active iff scope=future,
        with a captured matcher defaulting to this result's own parameters,
        verdicts, and tags), and stamps the result with a RuleResult.
        """,
        request=ClassifyRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=ClassifyResponseSerializer,
                description='The result was successfully classified',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Classification request validation failed (invalid category/scope, '
                    'or invalid issue data such as a missing title or bug key conflict)'
                ),
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'The user is not authenticated, or is authenticated but lacks '
                    'admin privileges required to manage classifications'
                ),
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'The result was not found, or the referenced issue ID does not exist'
                ),
            ),
        },
        tags=[RESULT_TAG],
    ),
)
