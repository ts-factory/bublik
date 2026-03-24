# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from bublik.analytics.models import AnalyticsEvent
from bublik.core.analytics.services import AnalyticsService


__all__ = [
    'AnalyticsCollectEventSerializer',
    'AnalyticsCollectRequestSerializer',
    'AnalyticsEventSerializer',
]


MAX_EVENTS_PER_BATCH = AnalyticsService.MAX_EVENTS_PER_BATCH
MAX_PAYLOAD_LENGTH = AnalyticsService.MAX_PAYLOAD_LENGTH


class AnalyticsEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalyticsEvent
        fields: typing.ClassVar[list[str]] = [
            'id',
            'event_uuid',
            'event_type',
            'event_name',
            'path',
            'anon_id',
            'session_id',
            'browser_name',
            'browser_version',
            'os_name',
            'user_agent',
            'app_version',
            'payload',
            'occurred_at',
            'created_at',
        ]


class AnalyticsCollectEventSerializer(serializers.Serializer):
    event_uuid = serializers.UUIDField(required=False)
    event_type = serializers.CharField(max_length=64)
    event_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    path = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=512,
    )
    anon_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    session_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    browser_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    browser_version = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=64,
    )
    os_name = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    user_agent = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=512,
    )
    app_version = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=64,
    )
    payload = serializers.JSONField(required=False)
    occurred_at = serializers.DateTimeField(required=False)

    def validate_payload(self, value):
        return AnalyticsService.validate_payload_size(value, MAX_PAYLOAD_LENGTH)


class AnalyticsCollectRequestSerializer(serializers.Serializer):
    events = AnalyticsCollectEventSerializer(many=True, allow_empty=False)

    def validate_events(self, value):
        if len(value) > MAX_EVENTS_PER_BATCH:
            msg = f'Only up to {MAX_EVENTS_PER_BATCH} events can be ingested per request'
            raise ValidationError(msg)
        return value
