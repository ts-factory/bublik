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
