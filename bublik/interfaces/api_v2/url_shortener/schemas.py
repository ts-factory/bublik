# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.url_shortener.serializers import (
    URLShortenerQuerySerializer,
    URLShortenerResponseSerializer,
)


URL_SHORTENER_TAG = 'URL Shortener'


url_shortener_view_schema = extend_schema_view(
    get=extend_schema(
        summary='Retrieve a short URL',
        description="""
        Return a short URL for the Bublik UI URL passed in the url query parameter.
        Create a stored endpoint if it does not already exist, or reuse an existing one.

        The supplied URL must start with the Bublik UI prefix for the current host.
        If it contains a project query parameter, include the project name in the
        short URL. The project parameter belongs inside the URL being shortened.
        """,
        parameters=[URLShortenerQuerySerializer],
        responses={
            200: OpenApiResponse(
                response=URLShortenerResponseSerializer,
                description='Short URL was successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description=(
                    'URL was not provided, has an incorrect prefix or invalid endpoint data, '
                    'or contains an invalid or nonexistent project ID'
                ),
            ),
        },
        tags=[URL_SHORTENER_TAG],
    ),
)
