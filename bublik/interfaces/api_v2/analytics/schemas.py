# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
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
    description='Authentication with administrator privileges is required',
)

invalid_bearer_response = OpenApiResponse(
    response=ErrorResponseSerializer,
    description='The Bearer token is invalid or expired',
)

invalid_datetime_response = OpenApiResponse(
    response=ErrorResponseSerializer,
    description='The from or to query parameter has an invalid datetime format',
)


ANALYTICS_FILTER_PARAMETERS = [
    *(
        OpenApiParameter(
            name=name,
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description=f'Filter by {name}. Separate multiple values with `;`.',
        )
        for name in ('event_type', 'event_name', 'path', 'anon_id', 'app_version')
    ),
    OpenApiParameter(
        name='search',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="""
        Case-insensitive substring search in `event_name`, `path`,
        `event_type`, or `anon_id`.
        """,
    ),
    OpenApiParameter(
        name='payload_search',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
        description="""
        Case-insensitive substring search in the JSON text of `payload`.
        """,
    ),
    OpenApiParameter(
        name='from',
        type=OpenApiTypes.DATETIME,
        location=OpenApiParameter.QUERY,
    ),
    OpenApiParameter(
        name='to',
        type=OpenApiTypes.DATETIME,
        location=OpenApiParameter.QUERY,
    ),
]

ANALYTICS_CHARTS_PARAMETERS = [
    *ANALYTICS_FILTER_PARAMETERS,
    OpenApiParameter(
        name='top_events_path',
        type=OpenApiTypes.STR,
        location=OpenApiParameter.QUERY,
    ),
]


analytics_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List analytics events',
        description="""
        Returns matching analytics events. Requires administrator privileges.
        """,
        parameters=ANALYTICS_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=AnalyticsEventSerializer(many=True),
                description='Analytics events were successfully retrieved',
            ),
            400: invalid_datetime_response,
            401: invalid_bearer_response,
            403: admin_required_response,
        },
        tags=[ANALYTICS_TAG],
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
        tags=[ANALYTICS_TAG],
    ),
    overview=extend_schema(
        summary='Retrieve analytics overview',
        description="""
        Returns summary metrics for matching events. Requires administrator privileges.
        """,
        parameters=ANALYTICS_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=AnalyticsOverviewResponseSerializer,
                description='The analytics overview was successfully retrieved',
            ),
            400: invalid_datetime_response,
            401: invalid_bearer_response,
            403: admin_required_response,
        },
        tags=[ANALYTICS_TAG],
    ),
    facets=extend_schema(
        summary='Retrieve analytics facets',
        description="""
        Returns filter values for matching events. Requires administrator privileges.
        """,
        parameters=ANALYTICS_FILTER_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=AnalyticsFacetsResponseSerializer,
                description='Analytics facets were successfully retrieved',
            ),
            400: invalid_datetime_response,
            401: invalid_bearer_response,
            403: admin_required_response,
        },
        tags=[ANALYTICS_TAG],
    ),
    charts=extend_schema(
        summary='Retrieve analytics charts',
        description="""
        Returns chart data for matching events. Requires administrator privileges.
        """,
        parameters=ANALYTICS_CHARTS_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=AnalyticsChartsResponseSerializer,
                description='Analytics chart data was successfully retrieved',
            ),
            400: invalid_datetime_response,
            401: invalid_bearer_response,
            403: admin_required_response,
        },
        tags=[ANALYTICS_TAG],
    ),
)
