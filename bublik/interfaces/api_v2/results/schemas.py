# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.results.serializers import (
    DropCacheRequestSerializer,
    DropCacheResponseSerializer,
    MarkRunCompromisedRequestSerializer,
    MarkRunCompromisedResponseSerializer,
    ResultArtifactsAndVerdictsResponseSerializer,
    ResultListQuerySerializer,
    ResultListResponseSerializer,
    ResultMeasurementsResponseSerializer,
    ResultRetrieveResponseSerializer,
    RunCommentRequestSerializer,
    RunCommentResponseSerializer,
    RunCommentValueResponseSerializer,
    RunCompromisedResponseSerializer,
    RunDetailsResponseSerializer,
    RunListQuerySerializer,
    RunListResponseSerializer,
    RunRequirementsResponseSerializer,
    RunSourceResponseSerializer,
    RunStatsQuerySerializer,
    RunStatsResponseSerializer,
    RunStatusResponseSerializer,
    UnmarkRunCompromisedResponseSerializer,
)


RUN_TAG = 'Runs'
RESULT_TAG = 'Results'


run_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List runs',
        description='''
        Returns a paginated list of test runs with project, timing, status,
        classification, metadata, tag, and summary statistics fields.
        Supports date, project, status, metadata, and expression filters.
        ''',
        parameters=[RunListQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=RunListResponseSerializer,
                description='Runs were successfully retrieved',
            ),
        },
        tags=[RUN_TAG],
    ),
    drop_cache=extend_schema(
        summary='Drop run cache',
        description='''
        Deletes selected cache entries for every run matching the current run
        filters and returns identifiers of affected runs.
        ''',
        request=DropCacheRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=DropCacheResponseSerializer,
                description='Run cache entries were successfully deleted',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Unknown cache key was provided',
            ),
        },
        tags=[RUN_TAG],
    ),
    nok_distribution=extend_schema(
        summary='Get NOK distribution',
        description='''
        Returns a boolean NOK flag for each test result in the run.
        ''',
        responses={
            200: OpenApiResponse(
                response={'type': 'array', 'items': {'type': 'boolean'}},
                description='NOK distribution was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    details=extend_schema(
        summary='Get run details',
        description='''
        Returns full details for a single run, including metadata, tags,
        branches, revisions, labels, configuration, status, and conclusion.
        ''',
        responses={
            200: OpenApiResponse(
                response=RunDetailsResponseSerializer,
                description='Run details were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    stats=extend_schema(
        summary='Get run statistics',
        description='''
        Returns the run result tree with aggregated pass/fail statistics and
        comments. The optional requirements filter limits statistics to tests
        matching the provided requirement list.
        ''',
        parameters=[RunStatsQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=RunStatsResponseSerializer,
                description='Run statistics were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    requirements=extend_schema(
        summary='List run requirements',
        description='''
        Returns sorted requirement values associated with a run.
        ''',
        responses={
            200: OpenApiResponse(
                response=RunRequirementsResponseSerializer,
                description='Run requirements were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    source=extend_schema(
        summary='Get run source URL',
        description='''
        Returns the external source URL associated with a run.
        ''',
        responses={
            200: OpenApiResponse(
                response=RunSourceResponseSerializer,
                description='Run source URL was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    status=extend_schema(
        summary='Get run status',
        description='''
        Returns the configured status value for a run.
        ''',
        responses={
            200: OpenApiResponse(
                response=RunStatusResponseSerializer,
                description='Run status was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    compromised=extend_schema(
        summary='Get run compromised status',
        description='''
        Returns whether a run is marked as compromised.
        ''',
        responses={
            200: OpenApiResponse(
                response=RunCompromisedResponseSerializer,
                description='Run compromised status was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
    comment=extend_schema(
        summary='Get run comment',
        description='''
        Returns the current run comment, or null if no comment exists.
        ''',
        responses={
            200: OpenApiResponse(
                response=RunCommentValueResponseSerializer,
                description='Run comment was successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Multiple comments were found for the run',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run was not found',
            ),
        },
        tags=[RUN_TAG],
    ),
)


mark_compromised_schema = extend_schema(
    summary='Mark run as compromised',
    description='''
    Marks a run as compromised using a required comment and optional bug
    reference data.
    ''',
    request=MarkRunCompromisedRequestSerializer,
    responses={
        200: OpenApiResponse(
            response=MarkRunCompromisedResponseSerializer,
            description='Run was successfully marked as compromised',
        ),
        400: OpenApiResponse(
            response=ErrorResponseSerializer,
            description='Compromised request validation failed',
        ),
        404: OpenApiResponse(
            response=ErrorResponseSerializer,
            description='Run was not found',
        ),
    },
    tags=[RUN_TAG],
)


unmark_compromised_schema = extend_schema(
    summary='Unmark run as compromised',
    description='''
    Removes the compromised marker from a run.
    ''',
    responses={
        200: OpenApiResponse(
            response=UnmarkRunCompromisedResponseSerializer,
            description='Run was successfully unmarked as compromised',
        ),
        400: OpenApiResponse(
            response=ErrorResponseSerializer,
            description='Run could not be unmarked',
        ),
    },
    tags=[RUN_TAG],
)


create_comment_schema = extend_schema(
    summary='Create run comment',
    description='''
    Creates or replaces a run comment.
    ''',
    request=RunCommentRequestSerializer,
    responses={
        201: OpenApiResponse(
            response=RunCommentResponseSerializer,
            description='Run comment was successfully created',
        ),
        400: OpenApiResponse(
            response=ErrorResponseSerializer,
            description='Run comment request validation failed',
        ),
    },
    tags=[RUN_TAG],
)


update_comment_schema = extend_schema(
    summary='Update run comment',
    description='''
    Creates or replaces a run comment.
    ''',
    request=RunCommentRequestSerializer,
    responses={
        200: OpenApiResponse(
            response=RunCommentResponseSerializer,
            description='Run comment was successfully updated',
        ),
        400: OpenApiResponse(
            response=ErrorResponseSerializer,
            description='Run comment request validation failed',
        ),
    },
    tags=[RUN_TAG],
)


delete_comment_schema = extend_schema(
    summary='Delete run comment',
    description='''
    Deletes the current run comment.
    ''',
    responses={
        204: OpenApiResponse(description='Run comment was successfully deleted'),
        400: OpenApiResponse(
            response=ErrorResponseSerializer,
            description='No comment exists for the run',
        ),
    },
    tags=[RUN_TAG],
)


result_viewset_schema = extend_schema_view(
    retrieve=extend_schema(
        summary='Get result details',
        description='''
        Returns full details for a single test iteration result, including
        expected and obtained results, artifacts, parameters, comments,
        requirements, error state, and measurement availability.
        ''',
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
        description='''
        Returns test iteration results matching the provided parent, test name,
        execution sequence, result status, classification, and requirement
        filters.
        ''',
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
        description='''
        Returns artifact and verdict meta values for a result.
        ''',
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
        description='''
        Returns measurement chart and table data for a result.
        ''',
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
