# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ConfigListResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    version = serializers.IntegerField()
    is_active = serializers.BooleanField()
    type = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    project = serializers.IntegerField(allow_null=True)
    created = serializers.DateTimeField()


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


class ErrorResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    message = serializers.CharField()


class ConfigPartialUpdateRequestSerializer(serializers.Serializer):
    name = serializers.CharField(required=False)
    is_active = serializers.BooleanField(required=False)
    description = serializers.CharField(required=False, allow_null=True)
    content = serializers.JSONField(required=False)
