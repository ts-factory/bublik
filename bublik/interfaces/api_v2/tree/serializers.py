# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class TreeNodeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    start = serializers.DateTimeField(format=None)
    name = serializers.CharField(allow_null=True, allow_blank=True)
    entity = serializers.CharField(allow_null=True)
    has_error = serializers.BooleanField()
    skipped = serializers.BooleanField()
    children = serializers.ListField(child=serializers.IntegerField(), required=False)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('children') is None:
            data.pop('children', None)
        return data


class TreeResponseSerializer(serializers.Serializer):
    tree = serializers.DictField(child=TreeNodeSerializer())
    main_package = serializers.IntegerField(allow_null=True)


class PathTreeSerializer(serializers.Serializer):
    path = serializers.ListField(child=serializers.IntegerField())
