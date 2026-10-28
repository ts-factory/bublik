# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.auth import auth_required, get_request_user, is_admin, require_login_session
from bublik.core.user_token import UserTokenService
from bublik.data.models import UserToken

from .schemas import user_token_schema
from .serializers import (
    IssuedUserTokenSerializer,
    UserTokenCreateRequestSerializer,
    UserTokenSerializer,
)


@user_token_schema
class UserTokenViewSet(GenericViewSet):
    """
    API for issuing and revoking personal access tokens.

    Requires the login cookie, so a leaked token can neither mint new tokens
    nor revoke the others.
    """

    http_method_names: typing.ClassVar[list[str]] = ['get', 'post', 'head', 'options']
    # For the schema generator only; each action builds its own queryset.
    queryset = UserToken.objects.none()
    # The default backend would advertise every model field, token_hash
    # included, as a query filter the lists do not apply.
    filter_backends: typing.ClassVar[list] = []

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        require_login_session(request)

    def _page_of(self, tokens):
        page = self.paginate_queryset(tokens)
        dtos = [UserTokenService.to_dto(token) for token in page]
        return self.get_paginated_response(UserTokenSerializer(dtos, many=True).data)

    @auth_required()
    def list(self, request):
        """The requesting user's own tokens."""
        return self._page_of(UserTokenService.tokens_of(get_request_user(request)))

    @auth_required()
    def create(self, request):
        """Issue a token. Its value is returned only here."""
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
        return self._page_of(UserTokenService.all_tokens())
