# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class LogJsonResponseSerializer(serializers.Serializer):
    url = serializers.CharField(
        allow_null=True,
        help_text='JSON log URL, or null when unavailable',
    )
    attachments_url = serializers.CharField(
        allow_null=True,
        help_text='Attachments JSON URL, or null when unavailable',
    )


class LogJsonQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text='Log page number; omit for the default log or use 0 for the combined log',
    )


class LogProxyQuerySerializer(serializers.Serializer):
    url = serializers.CharField(help_text='URL of the JSON log content to fetch')


class LogHtmlResponseSerializer(serializers.Serializer):
    url = serializers.CharField(
        allow_null=True,
        help_text='HTML log URL, or null when unavailable',
    )
