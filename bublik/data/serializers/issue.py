# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.db import transaction
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.data.models import (
    Issue,
    IssueExt,
    IssueRule,
    RuleResult,
    default_expected_for,
)
from bublik.data.models.issue import BUG_KEY_VALIDATOR


_AUDIT = ('created_by', 'created_at', 'updated_by', 'updated_at')
_MATCHER_FIELDS = ('project', 'issue', 'test', 'parameters', 'verdicts', 'tags')


class IssueExtSerializer(ModelSerializer):
    class Meta:
        model = IssueExt
        fields = ('id', 'key', 'status', 'title', 'synced_at')
        read_only_fields = ('status', 'title', 'synced_at')


class IssueSerializer(ModelSerializer):
    issue_ext = IssueExtSerializer(read_only=True)
    bug_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_null=True,
        allow_blank=True,
        validators=[BUG_KEY_VALIDATOR],
    )

    class Meta:
        model = Issue
        fields = (
            'id',
            'title',
            'description',
            'state',
            'issue_ext',
            'bug_key',
            'created_by',
            'created_at',
            'updated_by',
            'updated_at',
            'closed_by',
            'closed_at',
        )
        read_only_fields = (*_AUDIT, 'state', 'closed_by', 'closed_at', 'issue_ext')

    def validate_bug_key(self, value):
        if (
            self.instance is not None
            and RuleResult.objects.filter(issue_rule__issue=self.instance).exists()
        ):
            msg = 'Cannot change the bug key on an issue that already has classified results.'
            raise serializers.ValidationError(msg)
        if value:
            conflict = Issue.objects.filter(issue_ext__key=value)
            if self.instance is not None:
                conflict = conflict.exclude(pk=self.instance.pk)
            if conflict.exists():
                msg = 'This bug key is already linked to another issue.'
                raise serializers.ValidationError(msg)
        return value

    def create(self, validated_data):
        bug_key = validated_data.pop('bug_key', None)
        with transaction.atomic():
            issue = super().create(validated_data)
            self._sync_bug_key(issue, bug_key)
        return issue

    def update(self, instance, validated_data):
        bug_key = validated_data.pop('bug_key', None)
        with transaction.atomic():
            issue = super().update(instance, validated_data)
            if 'bug_key' in self.initial_data:
                self._sync_bug_key(issue, bug_key)
        return issue

    @staticmethod
    def _sync_bug_key(issue, bug_key):
        if not bug_key:
            issue.issue_ext = None
            issue.save()
            return
        ext, _ = IssueExt.objects.get_or_create(key=bug_key)
        issue.issue_ext = ext
        issue.save()


class IssueRuleSerializer(ModelSerializer):
    expected = serializers.BooleanField(required=False, allow_null=True)
    test_name = serializers.CharField(source='test.name', read_only=True)
    parameters = serializers.JSONField(required=False, default=dict, initial=dict)
    verdicts = serializers.JSONField(required=False, default=list, initial=list)
    tags = serializers.JSONField(required=False, default=list, initial=list)

    class Meta:
        model = IssueRule
        fields = (
            'id',
            'project',
            'issue',
            'category',
            'expected',
            'active',
            'test',
            'test_name',
            'parameters',
            'verdicts',
            'tags',
            'created_by',
            'created_at',
            'updated_by',
            'updated_at',
            'deactivated_by',
            'deactivated_at',
        )
        read_only_fields = (*_AUDIT, 'active', 'deactivated_by', 'deactivated_at')

    def validate(self, attrs):
        if self.instance is None:
            if 'expected' not in attrs and 'category' in attrs:
                attrs['expected'] = default_expected_for(attrs['category'])
            return attrs

        if self.instance.rule_results.exists():
            locked = set(attrs) & set(_MATCHER_FIELDS)
            if locked:
                raise serializers.ValidationError(
                    dict.fromkeys(
                        locked,
                        'Cannot change matcher fields on a rule that already '
                        'has classified results. Create a new rule instead.',
                    )
                )
        return attrs
