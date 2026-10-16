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
    children = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        help_text='Child node IDs; omitted for leaf nodes',
    )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('children') is None:
            data.pop('children', None)
        return data


class TreeResponseSerializer(serializers.Serializer):
    tree = serializers.DictField(
        child=TreeNodeSerializer(),
        help_text='Nodes keyed by result ID; JSON object keys are strings',
    )
    main_package = serializers.IntegerField(
        allow_null=True,
        help_text=(
            'Main package ID, or the requested node ID for a subtree; null when unavailable'
        ),
    )


class PathTreeSerializer(serializers.Serializer):
    path = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='Ancestor node IDs followed by the requested result ID',
    )
