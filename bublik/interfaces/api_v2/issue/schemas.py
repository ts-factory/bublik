# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.issue.serializers import (
    ActionResultSerializer,
    BulkIdsRequestSerializer,
    IssueListQuerySerializer,
    IssuePickerOptionSerializer,
    IssuePickerQuerySerializer,
    IssueRuleListQuerySerializer,
)


ISSUE_TAG = 'Issues'
ISSUE_RULE_TAG = 'Issue Rules'

_FORBIDDEN_DESCRIPTION = (
    'The user is not authenticated, or is authenticated but lacks admin '
    'privileges required to manage issues'
)


issue_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List issues',
        description="""
        Returns issues filtered by state, project, category, search, and
        creation/update date range, each with its bug reference and
        active-rule category and rule counts.
        """,
        parameters=[IssueListQuerySerializer],
        responses={200: IssueSerializer},
        tags=[ISSUE_TAG],
    ),
    retrieve=extend_schema(
        summary='Get issue details',
        responses={
            200: IssueSerializer,
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue was not found',
            ),
        },
        tags=[ISSUE_TAG],
    ),
    create=extend_schema(
        summary='Create an issue',
        request=IssueSerializer,
        responses={
            201: OpenApiResponse(
                response=IssueSerializer,
                description='Issue was successfully created',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue validation failed',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
        },
        tags=[ISSUE_TAG],
    ),
    partial_update=extend_schema(
        summary='Update an issue',
        request=IssueSerializer,
        responses={
            200: OpenApiResponse(
                response=IssueSerializer,
                description='Issue was successfully updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Issue validation failed, e.g. changing the bug key on an '
                    'issue that already has classified results'
                ),
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue was not found',
            ),
        },
        tags=[ISSUE_TAG],
    ),
    destroy=extend_schema(
        summary='Delete an issue',
        responses={
            204: OpenApiResponse(description='Issue was successfully deleted'),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue was not found',
            ),
        },
        tags=[ISSUE_TAG],
    ),
    close=extend_schema(
        summary='Close issues',
        description="""
        Closes any of the given issues that are currently open, deactivating
        their active rules.
        """,
        request=BulkIdsRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ActionResultSerializer,
                description='Issues were processed',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No issue IDs were provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
        },
        tags=[ISSUE_TAG],
    ),
    reopen=extend_schema(
        summary='Reopen issues',
        description="""
        Reopens any of the given issues that are currently closed.
        """,
        request=BulkIdsRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ActionResultSerializer,
                description='Issues were processed',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No issue IDs were provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
        },
        tags=[ISSUE_TAG],
    ),
)


issue_rule_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List issue rules',
        description="""
        Returns issue rules filtered by project, issue, search, category,
        active state, expected value, and creation date range.
        """,
        parameters=[IssueRuleListQuerySerializer],
        responses={200: IssueRuleSerializer},
        tags=[ISSUE_RULE_TAG],
    ),
    retrieve=extend_schema(
        summary='Get issue rule details',
        responses={
            200: IssueRuleSerializer,
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue rule was not found',
            ),
        },
        tags=[ISSUE_RULE_TAG],
    ),
    create=extend_schema(
        summary='Create an issue rule',
        request=IssueRuleSerializer,
        responses={
            201: OpenApiResponse(
                response=IssueRuleSerializer,
                description='Issue rule was successfully created',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Issue rule validation failed, e.g. the test has no results '
                    "in the issue's project"
                ),
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
        },
        tags=[ISSUE_RULE_TAG],
    ),
    partial_update=extend_schema(
        summary='Update an issue rule',
        request=IssueRuleSerializer,
        responses={
            200: OpenApiResponse(
                response=IssueRuleSerializer,
                description='Issue rule was successfully updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'Issue rule validation failed, e.g. changing matcher fields '
                    'on a rule that already has classified results'
                ),
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue rule was not found',
            ),
        },
        tags=[ISSUE_RULE_TAG],
    ),
    destroy=extend_schema(
        summary='Delete an issue rule',
        responses={
            204: OpenApiResponse(description='Issue rule was successfully deleted'),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Issue rule was not found',
            ),
        },
        tags=[ISSUE_RULE_TAG],
    ),
    activate=extend_schema(
        summary='Activate issue rules',
        description="""
        Activates any of the given issue rules that are currently inactive.
        """,
        request=BulkIdsRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ActionResultSerializer,
                description='Issue rules were processed',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No issue rule IDs were provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
        },
        tags=[ISSUE_RULE_TAG],
    ),
    deactivate=extend_schema(
        summary='Deactivate issue rules',
        description="""
        Deactivates any of the given issue rules that are currently active.
        """,
        request=BulkIdsRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ActionResultSerializer,
                description='Issue rules were processed',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No issue rule IDs were provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=_FORBIDDEN_DESCRIPTION,
            ),
        },
        tags=[ISSUE_RULE_TAG],
    ),
)


issue_picker_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List issue picker options',
        description="""
        Returns compact issue options for a selection widget: up to 20
        title/bug-key matches for the given search text, or the 10 most
        recently used issues (by latest classified result) when no search
        text is given. Optionally scoped to a project.
        """,
        parameters=[IssuePickerQuerySerializer],
        responses={200: IssuePickerOptionSerializer(many=True)},
        tags=[ISSUE_TAG],
    ),
)
