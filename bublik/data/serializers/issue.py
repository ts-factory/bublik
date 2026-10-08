# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.db.models import BooleanField, ExpressionWrapper, Q
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.core.classification import (
    RULE_SUPPRESSION_FILTER,
    Effect,
    RulesState,
    effect_for,
    rules_state_for,
)
from bublik.core.run.tests_organization import build_test_paths
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


def _display_name(user):
    """Full name, falling back to email; None if the field itself is null."""
    return (user.get_full_name() or user.email) if user else None


class IssueSerializer(ModelSerializer):
    bug_url = serializers.ReadOnlyField()
    project_name = serializers.CharField(source='project.name', read_only=True)
    rules = serializers.SerializerMethodField()
    rule_count = serializers.IntegerField(read_only=True, default=0)
    active_rule_count = serializers.IntegerField(read_only=True, default=0)
    result_count = serializers.IntegerField(read_only=True, default=0)
    rules_state = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    updated_by_name = serializers.SerializerMethodField()
    closed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Issue
        fields = (
            'id',
            'project',
            'project_name',
            'title',
            'description',
            'state',
            'bug_key',
            'bug_url',
            'rules',
            'rule_count',
            'active_rule_count',
            'result_count',
            'rules_state',
            'created_by',
            'created_by_name',
            'created_at',
            'updated_by',
            'updated_by_name',
            'updated_at',
            'closed_by',
            'closed_by_name',
            'closed_at',
        )
        read_only_fields = (*_AUDIT, 'state', 'closed_by', 'closed_at')

    def get_created_by_name(self, obj):
        return _display_name(obj.created_by)

    def get_updated_by_name(self, obj):
        return _display_name(obj.updated_by)

    def get_closed_by_name(self, obj):
        return _display_name(obj.closed_by)

    @extend_schema_field(
        {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'rule_id': {'type': 'integer'},
                    'category': {'type': 'string', 'enum': list(IssueCategory.values)},
                    'expected': {'type': 'boolean', 'nullable': True},
                    'effect': {'type': 'string', 'enum': list(Effect.values)},
                },
            },
        },
    )
    def get_rules(self, obj):
        """
        The issue's active rules, one entry per rule (no dedup by value).

        Reads `_active_rules_cache` when IssueViewSet's queryset prefetched it
        (avoids a query per issue on the /issues/ list); falls back to a direct
        query otherwise, e.g. right after creation. Either way, is_suppressed
        comes from an annotation built on RULE_SUPPRESSION_FILTER, not
        recomputed here - see effect_for().
        """
        if hasattr(obj, '_active_rules_cache'):
            return [
                {
                    'rule_id': rule.id,
                    'category': rule.category,
                    'expected': rule.expected,
                    'effect': effect_for(rule.is_suppressed, rule.expected, obj.state),
                }
                for rule in obj._active_rules_cache
            ]
        return [
            {
                'rule_id': r['id'],
                'category': r['category'],
                'expected': r['expected'],
                'effect': effect_for(r['is_suppressed'], r['expected'], obj.state),
            }
            for r in obj.rules.filter(active=True)
            .annotate(
                is_suppressed=ExpressionWrapper(
                    Q(**RULE_SUPPRESSION_FILTER),
                    output_field=BooleanField(),
                ),
            )
            .order_by('id')
            .values('id', 'category', 'expected', 'is_suppressed')
        ]

    @extend_schema_field({'type': 'string', 'enum': list(RulesState.values)})
    def get_rules_state(self, obj):
        """
        enforced/dormant/deactivated/unruled - see rules_state_for() next to
        SUPPRESSION_FILTER in core/classification.py, which this field and
        the `rules=` filter on GET /issues/ both read, so they can't drift
        apart.

        Reads has_any_rule/has_active_rule when IssueViewSet's queryset
        annotated them (avoids two extra queries per issue on the /issues/
        list); falls back to a direct query otherwise, e.g. right after
        creation.
        """
        if hasattr(obj, 'has_any_rule'):
            has_any_rule = obj.has_any_rule
            has_active_rule = obj.has_active_rule
        else:
            has_any_rule = obj.rules.exists()
            has_active_rule = obj.rules.filter(active=True).exists()
        return rules_state_for(has_any_rule, has_active_rule, obj.state)

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


class IssueRuleListSerializer(serializers.ListSerializer):
    """
    Resolves `test_path` for the whole page at once instead of row by row.
    DRF uses this automatically instead of the default ListSerializer
    whenever IssueRuleSerializer is instantiated with many=True, so no
    view-side changes are needed.
    """

    def to_representation(self, data):
        rules = list(data)
        test_paths = build_test_paths({rule.test_id for rule in rules})
        for rule in rules:
            rule._test_path = test_paths.get(rule.test_id, '')
        return super().to_representation(rules)


class IssueRuleSerializer(ModelSerializer):
    expected = serializers.BooleanField(required=False, allow_null=True)
    project = serializers.IntegerField(source='issue.project_id', read_only=True)
    project_name = serializers.CharField(source='issue.project.name', read_only=True)
    issue_title = serializers.CharField(source='issue.title', read_only=True)
    issue_state = serializers.CharField(source='issue.state', read_only=True)
    bug_key = serializers.ReadOnlyField(source='issue.bug_key')
    bug_url = serializers.ReadOnlyField(source='issue.bug_url')
    test_name = serializers.CharField(source='test.name', read_only=True)
    test_path = serializers.SerializerMethodField()
    parameters = serializers.JSONField(required=False, default=dict, initial=dict)
    verdicts = serializers.JSONField(required=False, default=list, initial=list)
    tags = serializers.JSONField(required=False, default=list, initial=list)

    class Meta:
        model = IssueRule
        list_serializer_class = IssueRuleListSerializer
        fields = (
            'id',
            'project',
            'project_name',
            'issue',
            'issue_title',
            'issue_state',
            'bug_key',
            'bug_url',
            'category',
            'expected',
            'active',
            'test',
            'test_name',
            'test_path',
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

    def get_test_path(self, obj):
        """
        '/'-joined package path for the rule's test, e.g. 'pkg1/pkg2/test'.

        Reads `_test_path` when IssueRuleListSerializer batched it for
        the whole page (avoids a per-row parent-chain walk); falls back to a
        direct lookup otherwise, e.g. on retrieve/create/update, where this
        serializer is used for a single instance (many=False), so there's no
        list to batch over.
        """
        if hasattr(obj, '_test_path'):
            return obj._test_path
        return build_test_paths([obj.test_id]).get(obj.test_id, '')

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
