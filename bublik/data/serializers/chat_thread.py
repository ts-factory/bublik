# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from typing import ClassVar

from rest_framework.serializers import ModelSerializer, SerializerMethodField

from bublik.data.models import ChatThread


__all__ = [
    'ChatThreadDetailSerializer',
    'ChatThreadListSerializer',
]


class ChatThreadListSerializer(ModelSerializer):
    """Lightweight thread representation for the sidebar list (no messages)."""

    # Whether the thread has a run streaming in the background right now. Read from
    # the Redis run store and passed in via the serializer ``context`` (a set of
    # streaming thread ids) so the sidebar can show a live indicator.
    is_streaming = SerializerMethodField()

    class Meta:
        model = ChatThread
        fields = (
            'id',
            'title',
            'is_archived',
            'is_streaming',
            'created',
            'updated',
        )

    def get_is_streaming(self, obj) -> bool:
        return str(obj.id) in self.context.get('streaming_thread_ids', set())


class ChatThreadDetailSerializer(ModelSerializer):
    """Full thread representation including the message list."""

    # Id of the run currently streaming for this thread (``None`` if idle). A
    # reloaded client shows a background-response indicator while it is present.
    active_run_id = SerializerMethodField()
    latest_run_status = SerializerMethodField()
    class Meta:
        model = ChatThread
        fields = (
            'id',
            'title',
            'is_archived',
            'messages',
            'active_run_id',
            'latest_run_status',
            'created',
            'updated',
        )
        extra_kwargs: ClassVar[dict] = {
            'title': {'required': False},
            'messages': {'read_only': True},
        }

    def get_active_run_id(self, obj) -> str | None:
        return self.context.get('active_run_id')

    def get_latest_run_status(self, obj) -> str | None:
        return self.context.get('latest_run_status')
