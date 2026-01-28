# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ConfigVersionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    version = serializers.IntegerField()
    is_active = serializers.BooleanField()
    description = serializers.CharField(allow_null=True)
    created = serializers.DateTimeField()


class AllVersionsResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    name = serializers.CharField()
    project = serializers.IntegerField(allow_null=True)
    all_config_versions = ConfigVersionSerializer(many=True)


class ConfigTypeNameSerializer(serializers.Serializer):
    type = serializers.CharField()
    name = serializers.CharField(required=False)
    required = serializers.BooleanField()
    description = serializers.CharField()


class AvailableTypesNamesResponseSerializer(serializers.Serializer):
    config_types_names = ConfigTypeNameSerializer(many=True)
