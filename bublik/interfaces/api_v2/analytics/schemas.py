# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers

from bublik.analytics.serializers import (
    AnalyticsCollectRequestSerializer,
    AnalyticsEventSerializer,
)
from bublik.interfaces.api_v2.analytics.serializers import (
    AnalyticsChartsResponseSerializer,
    AnalyticsFacetsResponseSerializer,
    AnalyticsOverviewResponseSerializer,
)
from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer


ANALYTICS_TAG = 'Analytics'


admin_required_response = OpenApiResponse(
    response=ErrorResponseSerializer,
    description='Administrator privileges are required to view analytics',
)

analytics_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List analytics events',
        description="""
        Returns matching analytics events. Requires administrator privileges.
        """,
        responses={
            200: OpenApiResponse(
                response=AnalyticsEventSerializer(many=True),
                description='Analytics events were successfully retrieved',
            ),
            403: admin_required_response,
        },
        tags=ANALYTICS_TAG,
    ),
    collect=extend_schema(
        summary='Collect analytics events',
        description="""
        Accepts a batch of analytics events and returns the number received.
        """,
        request=AnalyticsCollectRequestSerializer,
        responses={
            202: OpenApiResponse(
                response=inline_serializer(
                    name='AnalyticsCollectResponse',
                    fields={'received': serializers.IntegerField(min_value=0)},
                ),
                description='The batch was accepted',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid analytics event data was provided',
            ),
            429: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='The analytics collection rate limit was exceeded',
            ),
        },
        tags=ANALYTICS_TAG,
    ),
    overview=extend_schema(
        summary='Retrieve analytics overview',
        description="""
        Returns summary metrics for matching events. Requires administrator privileges.
        """,
        responses={
            200: OpenApiResponse(
                response=AnalyticsOverviewResponseSerializer,
                description='The analytics overview was successfully retrieved',
            ),
            403: admin_required_response,
        },
        tags=ANALYTICS_TAG,
    ),
    facets=extend_schema(
        summary='Retrieve analytics facets',
        description="""
        Returns filter values for matching events. Requires administrator privileges.
        """,
        responses={
            200: OpenApiResponse(
                response=AnalyticsFacetsResponseSerializer,
                description='Analytics facets were successfully retrieved',
            ),
            403: admin_required_response,
        },
        tags=ANALYTICS_TAG,
    ),
    charts=extend_schema(
        summary='Retrieve analytics charts',
        description="""
        Returns chart data for matching events. Requires administrator privileges.
        """,
        responses={
            200: OpenApiResponse(
                response=AnalyticsChartsResponseSerializer,
                description='Analytics chart data was successfully retrieved',
            ),
            403: admin_required_response,
        },
        tags=ANALYTICS_TAG,
    ),
)
