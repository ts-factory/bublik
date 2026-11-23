# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.dashboard.serializers import (
    DashboardDefaultModeResponseSerializer,
    DashboardListQuerySerializer,
    DashboardProjectQuerySerializer,
    DashboardResponseSerializer,
)
from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer


DASHBOARD_TAG = 'Dashboard'


dashboard_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='Retrieve the dashboard for one day',
        description="""
        Returns a single dashboard with column headers, run rows, and
        navigation handler descriptions. If no date is supplied, uses the
        latest available dashboard date for the selected project or all projects.
        An explicit date with no matching runs returns a dashboard with no rows.
        """,
        filters=False,
        parameters=[DashboardListQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=DashboardResponseSerializer,
                description='Dashboard data was successfully retrieved',
            ),
            204: OpenApiResponse(description='No dashboard date is available'),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Dashboard settings validation failed',
            ),
            500: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Dashboard data could not be loaded',
            ),
        },
        tags=[DASHBOARD_TAG],
    ),
    # The inherited detail action has no queryset or serializer in this view.
    retrieve=extend_schema(exclude=True),
    default_mode=extend_schema(
        summary='Retrieve the default dashboard layout',
        description="""
        Returns the configured number of days and columns for the default
        dashboard layout. Omit project to use global dashboard settings.
        """,
        parameters=[DashboardProjectQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=DashboardDefaultModeResponseSerializer,
                description='Default dashboard layout was successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Dashboard settings validation failed',
            ),
            500: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='The configured default dashboard mode is unknown',
            ),
        },
        tags=[DASHBOARD_TAG],
    ),
)
