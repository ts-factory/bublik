# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.user_mcp_server.serializers import (
    UserMcpServerCreateRequestSerializer,
    UserMcpServerSerializer,
    UserMcpServerUpdateRequestSerializer,
)


_ID_PARAM = OpenApiParameter(
    name='id',
    type=int,
    location=OpenApiParameter.PATH,
    description='ID of the MCP server',
)

user_mcp_server_schema = extend_schema_view(
    list=extend_schema(
        summary='List your MCP servers',
        description="""
        Return the requesting user's own MCP servers. Header values are never
        included: only the header names are shown.
        """,
        responses={
            200: OpenApiResponse(
                response=UserMcpServerSerializer(many=True),
                description='Servers were successfully retrieved',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not authenticated',
            ),
        },
        tags=['MCP servers'],
    ),
    create=extend_schema(
        summary='Register an MCP server',
        description="""
        Register a Streamable HTTP MCP server for your own chat runs. The host
        has to be permitted by the administrator's `user_mcp_servers` policy in
        the AI config. Header values are stored encrypted and cannot be read
        back afterwards.
        """,
        request=UserMcpServerCreateRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=UserMcpServerSerializer,
                description='The server was registered',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid name, URL or headers, a host the policy does not allow, '
                'or the per-user limit was reached',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not authenticated',
            ),
        },
        tags=['MCP servers'],
    ),
    partial_update=extend_schema(
        summary='Change an MCP server',
        description="""
        Change any subset of a server's fields. `headers` is merged: a value
        sets that header, `null` removes it, and headers not mentioned keep the
        value stored earlier, so a secret never has to be re-entered to change
        something else.
        """,
        request=UserMcpServerUpdateRequestSerializer,
        parameters=[_ID_PARAM],
        responses={
            200: OpenApiResponse(
                response=UserMcpServerSerializer,
                description='The server was updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid name, URL or headers, or a host the policy does not allow',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No such server, or it belongs to another user',
            ),
        },
        tags=['MCP servers'],
    ),
    destroy=extend_schema(
        summary='Delete an MCP server',
        request=None,
        parameters=[_ID_PARAM],
        responses={
            204: OpenApiResponse(description='The server was deleted'),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No such server, or it belongs to another user',
            ),
        },
        tags=['MCP servers'],
    ),
)
