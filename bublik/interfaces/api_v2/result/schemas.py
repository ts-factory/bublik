# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.result.serializers import (
    ClassifyOptionsRequestSerializer,
    ClassifyOptionsResponseSerializer,
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
    classify_options=extend_schema(
        summary='List what a rule classifying results may contain',
        description="""
        For results of one run, returns what the rules classifying them may
        contain, so that the matchers can be made and checked before
        `classify` is called. A rule belongs to one test and must match every
        result it classifies, so for each test among the results this is the
        parameters and the verdicts that all of its results have (any part of
        them may be used). The tags are those of the run: any of the
        important and relevant ones may be used, and the important ones are
        what a rule usually contains. Uses POST because the list of results
        can be too long for a URL.
        """,
        request=ClassifyOptionsRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ClassifyOptionsResponseSerializer,
                description='The options were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Unknown results, results of different runs, or a result that is '
                    'not a test result'
                ),
            ),
        },
        tags=[RESULT_TAG],
    ),
    classify=extend_schema(
        summary='Classify results',
        description="""
        Classifies results of one run. Finds/creates an Issue, creates an
        IssueRule for each of the given categories (active iff scope=future)
        and stamps the results with a RuleResult for each. A rule belongs to
        one test, so the results are grouped by test and every group gets its
        own rules. The `matchers` give the matcher of those rules for each
        test among the results, with all of its fields: the parameters, the
        verdicts and the tags (what is empty does not restrict the rule). A
        matcher may only contain what all the results of its test have and
        the tags of the run (see `classify_options`), so that a rule always
        matches the results it classifies. An identical rule that already
        exists (same test, matcher, category, disposition and active state)
        is reused instead of creating a copy.
        A given `expected` applies to every rule; without it each rule takes
        the default of its category.
        """,
        request=ClassifyRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=ClassifyResponseSerializer,
                description='The results were successfully classified',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Classification request validation failed (invalid categories/scope, '
                    'invalid issue data such as a missing title or bug key conflict, '
                    'unknown results, results of different runs, a result that is not '
                    'a test result, an unknown issue, or matchers that are missing for a '
                    'test or contain what not all its results have)'
                ),
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'The user is not authenticated, or is authenticated but lacks '
                    'admin privileges required to manage classifications'
                ),
            ),
        },
        tags=[RESULT_TAG],
    ),
)
