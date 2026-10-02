# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers

from bublik.data.models import Project


class PerformanceCheckQuerySerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(
        queryset=Project.objects.all(),
        required=False,
        help_text='Project ID to use for performance configuration lookup',
    )
