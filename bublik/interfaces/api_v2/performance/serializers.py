# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class PerformanceCheckQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=2_147_483_647,
        help_text='ID of the project whose configuration should be used',
    )


class PerformanceCheckResponseSerializer(serializers.Serializer):
    label = serializers.CharField()
    url = serializers.URLField(allow_null=True)
    timeout = serializers.IntegerField()