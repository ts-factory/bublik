# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ErrorResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    message = serializers.CharField()


class AllVersionsResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    name = serializers.CharField()
    project = serializers.IntegerField()
    all_config_versions = serializers.ListField(child=serializers.DictField())


class AvailableTypesNamesResponseSerializer(serializers.Serializer):
    config_types_names = serializers.ListField(child=serializers.DictField())
