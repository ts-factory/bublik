# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ProjectResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class ProjectCreateRequestSerializer(serializers.Serializer):
    name = serializers.CharField()


class ProjectUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)


class ProjectPartialUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)


class ProjectBadgeQuerySerializer(serializers.Serializer):
    label = serializers.CharField(required=False)
    metric = serializers.CharField(required=False)
