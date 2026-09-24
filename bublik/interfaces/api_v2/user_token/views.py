# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.auth import auth_required, get_bearer_token, get_request_user, is_admin
from bublik.core.user_token import UserTokenService

from .schemas import user_token_schema
from .serializers import (
    IssuedUserTokenSerializer,
    UserTokenCreateRequestSerializer,
    UserTokenSerializer,
)


@user_token_schema
class UserTokenViewSet(ViewSet):
    """
    API for issuing and revoking personal access tokens.
    """

    http_method_names: typing.ClassVar[list[str]] = ['get', 'post', 'head', 'options']

    @auth_required()
    def list(self, request):
        """The requesting user's own tokens."""
        tokens = UserTokenService.list_for_user(get_request_user(request))
        return Response(UserTokenSerializer(tokens, many=True).data)

    @auth_required()
    def create(self, request):
        """Issue a token. Its value is returned only here.

        Requires the login cookie, so a leaked token cannot mint new ones.
        """
        if get_bearer_token(request.headers.get('Authorization')):
            msg = 'Access tokens can only be issued from a login session'
            raise PermissionDenied(msg)
        request_serializer = UserTokenCreateRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        issued = UserTokenService.issue(
            user=get_request_user(request),
            name=request_serializer.validated_data['name'],
            expires_in=request_serializer.validated_data['expires_in'],
        )
        return Response(
            IssuedUserTokenSerializer(issued).data,
            status=status.HTTP_201_CREATED,
        )

    @auth_required()
    @action(detail=True, methods=['post'])
    def revoke(self, request, pk=None):
        """Revoke a token."""
        user = get_request_user(request)
        token = UserTokenService.revoke(
            token_id=pk,
            acting_user=user,
            as_admin=is_admin(user),
        )
        return Response(UserTokenSerializer(token).data)

    @auth_required(as_admin=True)
    @action(detail=False, methods=['get'])
    def all(self, request):
        """Every user's tokens."""
        return Response(UserTokenSerializer(UserTokenService.list_all(), many=True).data)
