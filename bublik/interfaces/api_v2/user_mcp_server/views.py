# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.auth import auth_required, get_request_user
from bublik.core.user_mcp_server import UserMcpServerService

from .schemas import user_mcp_server_schema
from .serializers import (
    UserMcpServerCreateRequestSerializer,
    UserMcpServerSerializer,
    UserMcpServerUpdateRequestSerializer,
)


@user_mcp_server_schema
class UserMcpServerViewSet(ViewSet):
    """
    API for the MCP servers a user attaches to their own chat runs.

    Header values are write-only.
    """

    http_method_names: typing.ClassVar[list[str]] = [
        'get',
        'post',
        'patch',
        'delete',
        'head',
        'options',
    ]

    @auth_required()
    def list(self, request):
        """The requesting user's own servers."""
        servers = UserMcpServerService.list_for_user(get_request_user(request))
        return Response(UserMcpServerSerializer(servers, many=True).data)

    @auth_required()
    def create(self, request):
        """Register a server."""
        request_serializer = UserMcpServerCreateRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        server = UserMcpServerService.create(
            user=get_request_user(request),
            name=data['name'],
            url=data['url'],
            headers=data['headers'],
            enabled=data['enabled'],
        )
        return Response(UserMcpServerSerializer(server).data, status=status.HTTP_201_CREATED)

    @auth_required()
    def partial_update(self, request, pk=None):
        """Change a server; omitted fields stay as they are."""
        request_serializer = UserMcpServerUpdateRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        data = request_serializer.validated_data

        kwargs = {key: data[key] for key in ('name', 'url', 'enabled') if key in data}
        server = UserMcpServerService.update(
            get_request_user(request),
            pk,
            headers=data.get('headers'),
            **kwargs,
        )
        return Response(UserMcpServerSerializer(server).data)

    @auth_required()
    def destroy(self, request, pk=None):
        """Delete a server."""
        UserMcpServerService.delete(get_request_user(request), pk)
        return Response(status=status.HTTP_204_NO_CONTENT)
