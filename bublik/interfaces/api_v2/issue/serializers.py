# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ActionResultSerializer(serializers.Serializer):
    requested = serializers.IntegerField()
    updated = serializers.IntegerField()
    unchanged = serializers.IntegerField()
    not_found = serializers.IntegerField()


class BulkIdsRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


class IssueListQuerySerializer(serializers.Serializer):
    state = serializers.CharField(required=False)
    project = serializers.IntegerField(required=False)
    category = serializers.CharField(required=False)
    search = serializers.CharField(required=False)
    created_after = serializers.DateField(required=False)
    created_before = serializers.DateField(required=False)
    updated_after = serializers.DateField(required=False)
    updated_before = serializers.DateField(required=False)


class IssueRuleListQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False)
    issue = serializers.IntegerField(required=False)
    search = serializers.CharField(required=False)
    category = serializers.CharField(required=False)
    active = serializers.CharField(required=False)
    expected = serializers.ChoiceField(
        choices=['expected', 'unexpected', 'none'],
        required=False,
    )
    created_after = serializers.DateField(required=False)
    created_before = serializers.DateField(required=False)


class IssuePickerQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False)
    search = serializers.CharField(required=False)


class IssuePickerOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    key = serializers.CharField(allow_null=True)
    category = serializers.CharField(allow_null=True)
