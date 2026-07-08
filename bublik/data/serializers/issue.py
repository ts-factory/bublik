# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from collections import Counter, defaultdict
from typing import ClassVar

from django.conf import settings
from django.db.models import BooleanField, Exists, ExpressionWrapper, OuterRef, Q
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
from bublik.core.utils import key_value_transforming
from bublik.data.models import (
    Issue,
    IssueCategory,
    IssueRule,
    IssueState,
    Meta,
    MetaResult,
    ResultType,
    RuleResult,
    TestArgument,
    TestIterationResult,
    default_expected_for,
)


_AUDIT = ('created_by', 'created_at', 'updated_by', 'updated_at')
_MATCHER_FIELDS = ('issue', 'test', 'parameters', 'verdicts', 'tags')


def _display_name(user):
    """Full name, falling back to email; None if the field itself is null."""
    return (user.get_full_name() or user.email) if user else None


class DistinctListField(serializers.ListField):
    """
    A list in which no value may come twice: a set that is sent as a list.
    A repeated value is rejected rather than dropped, so that what is stored
    is what the client sent.
    """

    def to_internal_value(self, data):
        values = super().to_internal_value(data)
        repeated = sorted(value for value, count in Counter(values).items() if count > 1)
        if repeated:
            msg = f'Repeated values: {", ".join(repeated)}.'
            raise serializers.ValidationError(msg)
        return values


def _unknown_tags(tags, project_id):
    """
    The tags that no run of the project has. A tag is spelled as the rules
    capture it ('name=value', or 'name' for a tag without a value), so the
    metas it may come from are looked up by name and value (an index), and a
    meta counts only if a run of the project carries it.
    """
    if not tags:
        return []
    delimiter = settings.KEY_VALUE_DELIMITER
    query = Q()
    for tag in tags:
        query |= Q(name=tag, value__isnull=True) | Q(name=tag, value='')
        name, sep, value = tag.partition(delimiter)
        if sep and value:
            query |= Q(name=name, value=value)
    known = {
        key_value_transforming(name, value)
        for name, value in Meta.objects.filter(query, type='tag')
        .filter(
            Exists(
                MetaResult.objects.filter(meta=OuterRef('pk'), result__project_id=project_id),
            ),
        )
        .values_list('name', 'value')
    }
    return [tag for tag in tags if tag not in known]


def _unknown_parameters(parameters, test_id):
    """The parameters ('name=value') that no iteration of the test has."""
    if not parameters:
        return []
    query = Q()
    for name, value in parameters.items():
        query |= Q(name=name, value=value)
    known = set(
        TestArgument.objects.filter(query, test_iterations__test_id=test_id)
        .values_list('name', 'value')
        .distinct(),
    )
    return [
        key_value_transforming(name, value)
        for name, value in parameters.items()
        if (name, value) not in known
    ]


def _unknown_verdicts(verdicts, test_id, project_id):
    """
    The verdicts that no result of the test in the project has. Each is
    looked for until its first result, rather than over all of them.
    """
    if not verdicts:
        return []
    known = set(
        Meta.objects.filter(type='verdict', name__isnull=True, value__in=set(verdicts))
        .filter(
            Exists(
                MetaResult.objects.filter(
                    meta=OuterRef('pk'),
                    result__project_id=project_id,
                    result__iteration__test_id=test_id,
                ),
            ),
        )
        .values_list('value', flat=True),
    )
    return list(dict.fromkeys(verdict for verdict in verdicts if verdict not in known))


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
        # Uniqueness within a project is checked in validate() instead.
        validators: ClassVar[list] = []

    def get_created_by_name(self, obj) -> str | None:
        return _display_name(obj.created_by)

    def get_updated_by_name(self, obj) -> str | None:
        return _display_name(obj.updated_by)

    def get_closed_by_name(self, obj) -> str | None:
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

        new_project = attrs.get('project')
        if self.instance is not None and new_project and new_project != self.instance.project:
            # The rules move along with the issue, so they must fit the new
            # project, as when a rule is created there: their tests must have
            # results in it, and their tags and verdicts must be in it.
            rules = list(self.instance.rules.values('test_id', 'tags', 'verdicts'))
            test_ids = {rule['test_id'] for rule in rules}
            tested_ids = set(
                TestIterationResult.objects.filter(
                    project=new_project,
                    iteration__test_id__in=test_ids,
                ).values_list('iteration__test_id', flat=True),
            )
            untested_paths = sorted(build_test_paths(test_ids - tested_ids).values())
            if untested_paths:
                msg = (
                    f'Tests of the issue rules have no results in project '
                    f'{new_project.name!r}: {", ".join(untested_paths)}.'
                )
                raise serializers.ValidationError({'project': msg})

            unknown_tags = _unknown_tags(
                sorted({tag for rule in rules for tag in rule['tags']}),
                new_project.id,
            )
            verdicts_by_test = defaultdict(set)
            for rule in rules:
                verdicts_by_test[rule['test_id']].update(rule['verdicts'] or ())
            unknown_verdicts = sorted(
                {
                    verdict
                    for test_id, verdicts in verdicts_by_test.items()
                    for verdict in _unknown_verdicts(verdicts, test_id, new_project.id)
                },
            )
            errors = []
            if unknown_tags:
                errors.append(
                    f'Tags of the issue rules are not among the tags of the runs of project '
                    f'{new_project.name!r}: {", ".join(unknown_tags)}.',
                )
            if unknown_verdicts:
                errors.append(
                    f'Verdicts of the issue rules are not among the verdicts of the results '
                    f'of their tests in project {new_project.name!r}: '
                    f'{", ".join(unknown_verdicts)}.',
                )
            if errors:
                raise serializers.ValidationError({'project': errors})

        # The title and the bug key are unique within a project. Checked here
        # rather than by the validators DRF derives from the model's
        # constraints, so that a clash is reported under the field that
        # clashes: theirs is a non-field error naming `project`, which a client
        # creating an issue inline (see classify) doesn't even send.
        if {'project', 'title', 'bug_key'} & set(attrs):
            project = attrs.get('project') or self.instance.project
            others = Issue.objects.filter(project=project)
            if self.instance is not None:
                others = others.exclude(pk=self.instance.pk)
            title = attrs.get('title', getattr(self.instance, 'title', None))
            bug_key = attrs.get('bug_key', getattr(self.instance, 'bug_key', None))
            errors = {}
            if others.filter(title=title).exists():
                errors['title'] = 'An issue with this title already exists in the project.'
            if bug_key is not None and others.filter(bug_key=bug_key).exists():
                errors['bug_key'] = 'An issue with this bug key already exists in the project.'
            if errors:
                raise serializers.ValidationError(errors)

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
    # Matcher values are compared with the stored ones exactly, so they are
    # taken as given, without trimming. They have no defaults: what a rule
    # matches is always given in full, so that an omitted field is never
    # mistaken for an empty one.
    parameters = serializers.DictField(
        child=serializers.CharField(allow_blank=True, trim_whitespace=False),
    )
    # Null means the verdicts are ignored, [] that the result must have none.
    verdicts = serializers.ListField(
        child=serializers.CharField(trim_whitespace=False),
        allow_null=True,
    )
    # A set of run tags: a tag given twice is rejected, and the order doesn't
    # matter, so they are kept sorted (see validate_tags()).
    tags = DistinctListField(
        child=serializers.CharField(trim_whitespace=False),
    )

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

    def get_test_path(self, obj) -> str:
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

    def validate_tags(self, value):
        # Kept in one order, so that the same set is stored, compared and
        # looked up the same way whatever order it came in.
        return sorted(value)

    def validate(self, attrs):
        if self.instance is None:
            if 'expected' not in attrs and 'category' in attrs:
                attrs['expected'] = default_expected_for(attrs['category'])
        elif self.instance.rule_results.exists():
            # A field sent with its current value is not a change, so that a
            # client may send the whole rule back.
            locked = [
                field
                for field in _MATCHER_FIELDS
                if field in attrs and attrs[field] != getattr(self.instance, field)
            ]
            if locked:
                raise serializers.ValidationError(
                    dict.fromkeys(
                        locked,
                        'Cannot change matcher fields on a rule that already '
                        'has classified results. Create a new rule instead.',
                    )
                )

        # Closing an issue deactivates its rules, so a rule can only be
        # attached to an open one (reopen the issue first).
        new_issue = attrs.get('issue')
        if (
            new_issue is not None
            and new_issue.state == IssueState.CLOSED
            and (self.instance is None or new_issue != self.instance.issue)
        ):
            msg = f'Issue #{new_issue.id} is closed. Reopen it before adding rules.'
            raise serializers.ValidationError({'issue': msg})

        # A rule matches the results of a test; packages and sessions are
        # not classified.
        new_test = attrs.get('test')
        if new_test is not None and new_test.result_type != ResultType.conv('test'):
            msg = f'{new_test.name!r} is a package or a session, not a test.'
            raise serializers.ValidationError({'test': msg})

        if 'issue' in attrs or 'test' in attrs:
            issue = attrs.get('issue') or self.instance.issue
            test = attrs.get('test') or self.instance.test
            if not TestIterationResult.objects.filter(
                project=issue.project,
                iteration__test=test,
            ).exists():
                msg = f'Test {test.name!r} has no results in project {issue.project.name!r}.'
                raise serializers.ValidationError({'test': msg})

        self._check_matcher_values(attrs)
        return attrs

    def _check_matcher_values(self, attrs):
        """
        Check that the matcher holds only what is in the database: the tags
        of the runs of the issue's project, the parameters of the iterations
        of the test, and the verdicts of the results of the test in the
        project. A rule with anything else would match nothing.

        Checked when a rule is created, and when a matcher field changes (a
        field sent with its current value is not a change): then the whole
        matcher is checked against the resulting issue and test.
        """
        if self.instance is not None and not any(
            field in attrs and attrs[field] != getattr(self.instance, field)
            for field in _MATCHER_FIELDS
        ):
            return

        def current(field):
            return attrs[field] if field in attrs else getattr(self.instance, field)

        project = current('issue').project
        test = current('test')
        errors = {}
        unknown_tags = _unknown_tags(current('tags'), project.id)
        if unknown_tags:
            errors['tags'] = (
                f'Not among the tags of the runs of project {project.name!r}: '
                f'{", ".join(unknown_tags)}.'
            )
        unknown_parameters = _unknown_parameters(current('parameters'), test.id)
        if unknown_parameters:
            errors['parameters'] = (
                f'Not among the parameters of test {test.name!r}: '
                f'{", ".join(unknown_parameters)}.'
            )
        unknown_verdicts = _unknown_verdicts(current('verdicts'), test.id, project.id)
        if unknown_verdicts:
            errors['verdicts'] = (
                f'Not among the verdicts of the results of test {test.name!r} in project '
                f'{project.name!r}: {", ".join(unknown_verdicts)}.'
            )
        if errors:
            raise serializers.ValidationError(errors)
