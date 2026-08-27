# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.project.serializers import (
    ProjectBadgeQuerySerializer,
    ProjectCreateRequestSerializer,
    ProjectPartialUpdateRequestSerializer,
    ProjectResponseSerializer,
    ProjectUpdateRequestSerializer,
)


PROJECT_TAG = 'Project'


project_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List projects',
        description="""
        Return a list of available projects ordered by project ID.
        """,
        responses={
            200: OpenApiResponse(
                response=ProjectResponseSerializer(many=True),
                description='Projects were successfully retrieved',
            ),
        },
        tags=[PROJECT_TAG],
    ),
    create=extend_schema(
        summary='Create project',
        description="""
        Create a new project using the provided project payload.
        Admin permissions are required.
        """,
        request=ProjectCreateRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=ProjectResponseSerializer,
                description='Project was successfully created',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid project data was provided',
            ),
            401: OpenApiResponse(
                response=ErrorResponseSerializer, description='User is unauthorized'
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to create projects',
            ),
        },
        tags=[PROJECT_TAG],
    ),
    retrieve=extend_schema(
        summary='Get project name and ID',
        description="""
        Return project details by project ID.
        """,
        responses={
            200: OpenApiResponse(
                response=ProjectResponseSerializer,
                description='Project details were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project was not found',
            ),
        },
        tags=[PROJECT_TAG],
    ),
    update=extend_schema(
        summary='Update project',
        description="""
        Replace project data by project ID using the provided project payload.
        Admin permissions are required.
        """,
        request=ProjectUpdateRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ProjectResponseSerializer,
                description='Project was successfully updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid project data was provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to update projects',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project was not found',
            ),
        },
        tags=[PROJECT_TAG],
    ),
    partial_update=extend_schema(
        summary='Partial update project',
        description="""
        Partially update project data by project ID.
        Admin permissions are required.
        """,
        request=ProjectPartialUpdateRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=ProjectResponseSerializer,
                description='Project was successfully updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid project data was provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to update projects',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project was not found',
            ),
        },
        tags=[PROJECT_TAG],
    ),
    destroy=extend_schema(
        summary='Delete project',
        description="""
        Delete project by project ID. Admin permissions are required.

        The project cannot be deleted if it has linked runs.
        """,
        responses={
            204: OpenApiResponse(
                response=None,
                description='Project was successfully deleted',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project has linked runs',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Admin permissions are required',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project was not found',
            ),
        },
        tags=[PROJECT_TAG],
    ),
    badge=extend_schema(
        summary='Get project badge',
        description="""
        Return an SVG badge for the latest top-level run of the selected
        project.

        The optional label query parameter controls the left-side badge
        text. The optional metric query parameter controls the displayed
        metric: passed, unexpected, total, or rate. If metric is not
        provided or is unknown, the badge shows the latest run conclusion
        with NOK count.
        """,
        parameters=[ProjectBadgeQuerySerializer],
        responses={
            (200, 'image/svg+xml'): OpenApiResponse(
                description='SVG badge was successfully generated',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project was not found',
            ),
        },
        tags=[PROJECT_TAG],
    ),
)
