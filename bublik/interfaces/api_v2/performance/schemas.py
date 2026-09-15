# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.performance.serializers import (
    PerformanceCheckQuerySerializer,
    PerformanceCheckResponseSerializer,
)


PERFORMANCE_TAG = 'Performance'


performance_check_view_schema = extend_schema_view(
    get=extend_schema(
        summary='Get performance check configuration',
        description="""
        Return labels, URLs, and timeouts for the views included in the
        performance check.

        The optional project parameter selects the project configuration
        used to build the history view URLs. The global configuration is
        used as a fallback. History URLs are null when a suitable history
        search example is not configured.
        """,
        parameters=[PerformanceCheckQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=PerformanceCheckResponseSerializer(many=True),
                description='Performance check configuration was successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Project ID is invalid',
            ),
        },
        tags=[PERFORMANCE_TAG],
    ),
)
