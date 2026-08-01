# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.ai import run_store
from bublik.core.auth import auth_required, get_user_by_access_token
from bublik.data.models import ChatThread
from bublik.data.serializers import ChatThreadDetailSerializer, ChatThreadListSerializer


class ChatThreadViewSet(ModelViewSet):
    """
    API for managing the requesting user's AI chat threads.

    Threads are private per user. The whole message list is stored as a JSON
    blob, maintained exclusively by the AI run backend. ``PATCH`` renames and
    archives threads; clients never write conversation history directly.
    """

    pagination_class = None
    queryset = ChatThread.objects.all()
    serializer_class = ChatThreadDetailSerializer
    http_method_names: typing.ClassVar[list[str]] = [
        'get',
        'patch',
        'delete',
        'head',
        'options',
    ]

    def get_serializer_class(self):
        if self.action == 'list':
            return ChatThreadListSerializer
        return ChatThreadDetailSerializer

    def _get_user(self):
        access_token = self.request.COOKIES.get('access_token')
        return get_user_by_access_token(access_token)

    def get_queryset(self):
        user = self._get_user()
        if not user:
            return ChatThread.objects.none()
        return ChatThread.objects.filter(user=user)

    def _get_owned_or_none(self, pk, user):
        try:
            thread = ChatThread.objects.filter(pk=pk).first()
        except (ValidationError, ValueError):
            return None
        if thread is not None and thread.user_id != user.id:
            # Hide existence of another user's thread.
            raise NotFound
        return thread

    @auth_required()
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        include_archived = request.query_params.get('archived', '').lower() in (
            '1',
            'true',
            'yes',
        )
        if not include_archived:
            queryset = queryset.filter(is_archived=False)
        threads = list(queryset)
        streaming_ids = run_store.active_streaming_threads(
            [str(thread.id) for thread in threads]
        )
        serializer = ChatThreadListSerializer(
            threads,
            many=True,
            context={'streaming_thread_ids': streaming_ids},
        )
        return Response(serializer.data)

    @auth_required()
    def retrieve(self, request, *args, **kwargs):
        user = self._get_user()
        thread = self._get_owned_or_none(kwargs['pk'], user)
        if thread is None:
            raise NotFound
        active_run_id = run_store.active_run(str(thread.id))
        latest_run_status = run_store.latest_run_status(str(thread.id))
        return Response(
            ChatThreadDetailSerializer(
                thread,
                context={
                    'active_run_id': active_run_id,
                    'latest_run_status': latest_run_status,
                },
            ).data
        )

    @auth_required()
    def partial_update(self, request, *args, **kwargs):
        # PATCH renames / archives the thread.
        user = self._get_user()
        thread = self._get_owned_or_none(kwargs['pk'], user)
        if thread is None:
            raise NotFound
        serializer = ChatThreadDetailSerializer(thread, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @auth_required()
    def destroy(self, request, *args, **kwargs):
        user = self._get_user()
        thread = self._get_owned_or_none(kwargs['pk'], user)
        if thread is None:
            raise NotFound
        thread.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
