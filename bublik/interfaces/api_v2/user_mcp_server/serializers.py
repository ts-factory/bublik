# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class UserMcpServerSerializer(serializers.Serializer):
    """A listed server. Never includes header values."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    slug = serializers.CharField()
    url = serializers.CharField()
    enabled = serializers.BooleanField()
    header_names = serializers.ListField(child=serializers.CharField())
    created = serializers.DateTimeField()
    updated = serializers.DateTimeField()


class UserMcpServerCreateRequestSerializer(serializers.Serializer):
    """The request body for registering a server."""

    name = serializers.CharField(max_length=64)
    url = serializers.CharField(max_length=2048)
    enabled = serializers.BooleanField(required=False, default=True)
    headers = serializers.DictField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        default=dict,
        help_text='Full header mapping. Values are stored encrypted and never returned.',
    )


class UserMcpServerUpdateRequestSerializer(serializers.Serializer):
    """The request body for a partial update."""

    name = serializers.CharField(max_length=64, required=False)
    url = serializers.CharField(max_length=2048, required=False)
    enabled = serializers.BooleanField(required=False)
    headers = serializers.DictField(
        child=serializers.CharField(allow_blank=False, allow_null=True),
        required=False,
        help_text=(
            'Headers to change: a value sets that header, null removes it, '
            'headers not mentioned keep their stored value.'
        ),
    )
