# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.report.serializers import (
    ReportConfigListResponseSerializer,
    ReportRetrieveQuerySerializer,
    ReportRetrieveResponseSerializer,
)


report_viewset_schema = extend_schema_view(
    configs=extend_schema(
        summary='List of configurations',
        description="""
        Return a list of active configs that can be
        used to build a report on the current run.
        """,
        responses={
            200: OpenApiResponse(
                response=ReportConfigListResponseSerializer,
                description='Configurations were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Current run does not exist',
            ),
        },
        tags=['Report'],
    ),
    retrieve=extend_schema(
        summary='Generate run report',
        description="""
        Generates a report for the selected run using the report configuration
        passed in the config query parameter.
        """,
        parameters=[ReportRetrieveQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=ReportRetrieveResponseSerializer,
                description='Report was successfully generated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Report config was not provided or is invalid',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Run or report config was not found',
            ),
        },
        tags=['Report'],
    ),
)
