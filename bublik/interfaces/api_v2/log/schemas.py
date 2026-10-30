# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.data.serializers import TestIterationResultSerializer
from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.log.serializers import (
    LogHtmlResponseSerializer,
    LogJsonQuerySerializer,
    LogJsonResponseSerializer,
    LogProxyQuerySerializer,
)


LOG_TAG = 'Logs'


log_viewset_schema = extend_schema_view(
    retrieve=extend_schema(
        summary='Get log result',
        description="""
        Returns the test iteration result associated with a log.
        """,
        responses={
            200: OpenApiResponse(
                response=TestIterationResultSerializer,
                description='Result was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[LOG_TAG],
    ),
    json=extend_schema(
        summary='Get JSON log URLs',
        description="""
        Returns URLs for the JSON log and its attachments, when available.
        Unavailable URLs are null. Omit page to select the default log,
        use 0 for the combined log, or a positive integer for a specific page.
        """,
        parameters=[LogJsonQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=LogJsonResponseSerializer,
                description='Log URLs were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Page parameter is invalid',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[LOG_TAG],
    ),
    html=extend_schema(
        summary='Get HTML log URL',
        description="""
        Returns an object with the HTML log URL; url is null when unavailable.
        """,
        responses={
            200: OpenApiResponse(
                response=LogHtmlResponseSerializer,
                description='Log URL was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[LOG_TAG],
    ),
    proxy=extend_schema(
        summary='Fetch JSON log content',
        description="""
        Forwards the request to the given URL and returns the remote JSON content
        without wrapping it in an object. The response may be an object, array,
        string, number, or boolean.

        Returns HTTP 200 with an empty body when the remote file is not found
        or its JSON content is null.
        """,
        parameters=[LogProxyQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.ANY,
                description='Remote JSON content, or an empty body when unavailable or null',
            ),
            422: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='URL parameter is missing or empty',
            ),
            502: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Remote content could not be fetched or is not valid JSON',
            ),
        },
        tags=[LOG_TAG],
    ),
)
