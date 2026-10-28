# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.core.pagination import DefaultPageNumberPagination
from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.user_token.serializers import (
    IssuedUserTokenSerializer,
    UserTokenCreateRequestSerializer,
    UserTokenSerializer,
)


# The generator adds these to `list` by itself, but not to a custom action.
_PAGE_PARAMETERS = [
    OpenApiParameter(
        DefaultPageNumberPagination.page_query_param,
        int,
        description=DefaultPageNumberPagination.page_query_description,
    ),
    OpenApiParameter(
        DefaultPageNumberPagination.page_size_query_param,
        int,
        description=DefaultPageNumberPagination.page_size_query_description,
    ),
]


user_token_schema = extend_schema_view(
    list=extend_schema(
        summary='List your access tokens',
        description="""
        Return the requesting user's own personal access tokens. Token values
        are never included: a value is shown once, when the token is created,
        and only its hash is stored.

        Like every call here, this needs the login session: a token cannot
        list, issue or revoke tokens.
        """,
        responses={
            200: OpenApiResponse(
                response=UserTokenSerializer(many=True),
                description='Tokens were successfully retrieved',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not authenticated, or authenticated with a token',
            ),
        },
        tags=['Access tokens'],
    ),
    create=extend_schema(
        summary='Issue an access token',
        description="""
        Create a token and return its value. This is the only response that
        ever carries the value -- it cannot be retrieved again afterwards.
        `expires_in` must be given: null means the token never expires.
        """,
        request=UserTokenCreateRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=IssuedUserTokenSerializer,
                description='The token was created; `token` holds its only copy',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid name or expiry, or the active token limit was reached',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not authenticated, or authenticated with a token',
            ),
            429: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Too many tokens were created in the past hour',
            ),
        },
        tags=['Access tokens'],
    ),
    revoke=extend_schema(
        summary='Revoke an access token',
        description="""
        Revoke a token, effective on the very next request made with it. The
        record is kept, showing who revoked it. Admins may revoke any user's
        token; everyone else only their own.
        """,
        request=None,
        parameters=[
            OpenApiParameter(
                name='id',
                type=int,
                location=OpenApiParameter.PATH,
                description='ID of the token to revoke',
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=UserTokenSerializer,
                description='The token was revoked',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not authenticated, or authenticated with a token',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No such token, or it belongs to another user',
            ),
        },
        tags=['Access tokens'],
    ),
    all=extend_schema(
        summary="List every user's access tokens",
        description="""
        Admin view of all tokens across all users. Values are never included.
        """,
        parameters=_PAGE_PARAMETERS,
        responses={
            200: OpenApiResponse(
                response=UserTokenSerializer(many=True),
                description='Tokens were successfully retrieved',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not authenticated, authenticated with a token, or not an admin',
            ),
        },
        tags=['Access tokens'],
    ),
)
