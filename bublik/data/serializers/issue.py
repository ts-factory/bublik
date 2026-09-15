# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.data.models import (
    Issue,
    IssueCategory,
    IssueRule,
    RuleResult,
    TestIterationResult,
    default_expected_for,
)


_AUDIT = ('created_by', 'created_at', 'updated_by', 'updated_at')
_MATCHER_FIELDS = ('issue', 'test', 'parameters', 'verdicts', 'tags')


class IssueSerializer(ModelSerializer):
    bug_url = serializers.ReadOnlyField()
    categories = serializers.SerializerMethodField()
    rule_count = serializers.IntegerField(read_only=True, default=0)
    active_rule_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Issue
        fields = (
            'id',
            'project',
            'title',
            'description',
            'state',
            'bug_key',
            'bug_url',
            'categories',
            'rule_count',
            'active_rule_count',
            'created_by',
            'created_at',
            'updated_by',
            'updated_at',
            'closed_by',
            'closed_at',
        )
        read_only_fields = (*_AUDIT, 'state', 'closed_by', 'closed_at')

    @extend_schema_field(
        {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'category': {'type': 'string', 'enum': list(IssueCategory.values)},
                    'expected': {'type': 'boolean', 'nullable': True},
                },
            },
        },
    )
    def get_categories(self, obj):
        """
        Distinct (category, expected) pairs from the issue's active rules.

        Reads `_active_rules_cache` when IssueViewSet's queryset prefetched it
        (avoids a query per issue on the /issues/ list); falls back to a direct
        query otherwise, e.g. right after creation.
        """
        if hasattr(obj, '_active_rules_cache'):
            pairs = {(rule.category, rule.expected) for rule in obj._active_rules_cache}
            return [
                {'category': category, 'expected': expected} for category, expected in pairs
            ]
        return list(obj.rules.filter(active=True).values('category', 'expected').distinct())

    def validate_bug_key(self, value):
        return value or None

    def validate(self, attrs):
        has_classified_results = (
            self.instance is not None
            and RuleResult.objects.filter(
                issue_rule__issue=self.instance,
            ).exists()
        )

        if (
            has_classified_results
            and 'bug_key' in attrs
            and attrs['bug_key'] != self.instance.bug_key
        ):
            msg = 'Cannot change the bug key on an issue that already has classified results.'
            raise serializers.ValidationError({'bug_key': msg})

        if (
            has_classified_results
            and 'project' in attrs
            and attrs['project'] != self.instance.project
        ):
            msg = 'Cannot change the project on an issue that already has classified results.'
            raise serializers.ValidationError({'project': msg})

        return attrs


class IssueRuleSerializer(ModelSerializer):
    expected = serializers.BooleanField(required=False, allow_null=True)
    project = serializers.IntegerField(source='issue.project_id', read_only=True)
    issue_title = serializers.CharField(source='issue.title', read_only=True)
    bug_key = serializers.ReadOnlyField(source='issue.bug_key')
    bug_url = serializers.ReadOnlyField(source='issue.bug_url')
    parameters = serializers.JSONField(required=False, default=dict, initial=dict)
    verdicts = serializers.JSONField(required=False, default=list, initial=list)
    tags = serializers.JSONField(required=False, default=list, initial=list)

    class Meta:
        model = IssueRule
        fields = (
            'id',
            'project',
            'issue',
            'issue_title',
            'bug_key',
            'bug_url',
            'category',
            'expected',
            'active',
            'test',
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
        elif self.instance.rule_results.exists():
            locked = set(attrs) & set(_MATCHER_FIELDS)
            if locked:
                raise serializers.ValidationError(
                    dict.fromkeys(
                        locked,
                        'Cannot change matcher fields on a rule that already '
                        'has classified results. Create a new rule instead.',
                    )
                )

        if 'issue' in attrs or 'test' in attrs:
            issue = attrs.get('issue') or self.instance.issue
            test = attrs.get('test') or self.instance.test
            if not TestIterationResult.objects.filter(
                project=issue.project,
                iteration__test=test,
            ).exists():
                msg = f'Test {test.name!r} has no results in project {issue.project.name!r}.'
                raise serializers.ValidationError({'test': msg})

        return attrs
