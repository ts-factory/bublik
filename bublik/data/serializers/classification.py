# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.data.models import (
    Issue,
    IssueExt,
    IssueRule,
    default_expected_for,
)


_AUDIT = ('created_by', 'created_at', 'updated_by', 'updated_at')


class IssueExtSerializer(ModelSerializer):
    class Meta:
        model = IssueExt
        fields = ('id', 'key', 'status', 'title', 'synced_at')
        read_only_fields = ('status', 'title', 'synced_at')


class IssueSerializer(ModelSerializer):
    issue_ext = IssueExtSerializer(read_only=True)

    class Meta:
        model = Issue
        fields = (
            'id', 'title', 'description', 'state', 'issue_ext',
            'created_by', 'created_at', 'updated_by', 'updated_at',
            'closed_by', 'closed_at',
        )
        read_only_fields = (*_AUDIT, 'closed_by', 'closed_at', 'issue_ext')


class IssueRuleSerializer(ModelSerializer):
    expected = serializers.BooleanField(required=False)

    class Meta:
        model = IssueRule
        fields = (
            'id', 'project', 'issue', 'category', 'expected', 'active', 'test',
            'match_parameters', 'match_verdicts', 'match_important_tags',
            'match_all_tags', 'parameters', 'verdicts', 'tags',
            'created_by', 'created_at', 'updated_by', 'updated_at',
            'deactivated_by', 'deactivated_at',
        )
        read_only_fields = (*_AUDIT, 'deactivated_by', 'deactivated_at')

    def validate(self, attrs):
        # `expected` defaults from category when the client omits it.
        if 'expected' not in attrs and 'category' in attrs:
            attrs['expected'] = default_expected_for(attrs['category'])
        return attrs
