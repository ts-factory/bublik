# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from collections import Counter
import typing

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, F, Max, Q
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from bublik.core.auth import check_action_permission, get_user_by_access_token
from bublik.core.cache import RunCache
from bublik.core.classification import (
    DISPOSITION_TO_EXPECTED,
    RulesState,
    active_rule_subquery,
    active_rules_prefetch,
    any_rule_subquery,
    disposition_query,
    effect_for,
    rules_state_query,
)
from bublik.core.filter_backends import StableOrderingFilter
from bublik.core.run.classification import ClassificationService
from bublik.core.run.tests_organization import build_test_paths, get_test_ids_by_name
from bublik.data.models import (
    Issue,
    IssueCategory,
    IssueRule,
    IssueState,
    ResultType,
    RuleResult,
    Test,
    TestIterationResult,
)
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
from bublik.interfaces.api_v2.issue.filters import IssueFilterSet, IssueRuleFilterSet
from bublik.interfaces.api_v2.issue.schemas import (
    issue_picker_viewset_schema,
    issue_rule_viewset_schema,
    issue_viewset_schema,
    test_picker_viewset_schema,
)
from bublik.interfaces.api_v2.issue.serializers import (
    ActionResultSerializer,
    IssuePickerOptionSerializer,
    TestPickerOptionSerializer,
)


def _actor(request):
    return get_user_by_access_token(request.COOKIES.get('access_token'))


def _action_ids(request, label):
    ids = request.data.get('ids')
    if not isinstance(ids, list) or not ids:
        raise ValidationError({'ids': f'Expected a non-empty list of {label} IDs.'})
    return ids


def _action_summary(ids, existing_ids, updated_ids):
    data = {
        'requested': len(ids),
        'updated': len(updated_ids),
        'unchanged': len(existing_ids) - len(updated_ids),
        'not_found': len(ids) - len(existing_ids),
    }
    return ActionResultSerializer(data).data


def _grouped_counts(qs, group_field, count_field='id'):
    """{value: count} for `qs` grouped by `group_field`, counting distinct `count_field`."""
    # Clear the default ordering: ORDER BY fields join GROUP BY and would
    # split every group into one row per timestamp.
    rows = qs.order_by().values(group_field).annotate(n=Count(count_field, distinct=True))
    return {row[group_field]: row['n'] for row in rows}


def _facets(request, filterset_class, base_qs, facet_params, count_fn):
    """For each dim, apply filterset_class with only that dim's own param removed."""
    facets = {}
    for dim_key, param_name in facet_params.items():
        params = request.query_params.copy()
        params.pop(param_name, None)
        qs = filterset_class(data=params, queryset=base_qs, request=request).qs
        facets[dim_key] = count_fn(dim_key, qs)
    return Response(facets)


_ISSUE_FACET_PARAMS = {
    'state': 'state',
    'categories': 'category',
    'rules': 'rules',
    'project': 'project',
}


def _issue_facet_counts(dim_key, qs):
    if dim_key == 'state':
        counts = _grouped_counts(qs, 'state')
        return {state: counts.get(state, 0) for state in IssueState.values}
    if dim_key == 'categories':
        counts = _grouped_counts(
            IssueRule.objects.filter(issue__in=qs, active=True),
            'category',
            count_field='issue',
        )
        return {category: counts.get(category, 0) for category in IssueCategory.values}
    if dim_key == 'rules':
        return {
            rule_state: qs.filter(rules_state_query([rule_state])).count()
            for rule_state in RulesState.values
        }
    return _grouped_counts(qs, 'project_id')


@issue_viewset_schema
class IssueViewSet(ModelViewSet):
    serializer_class = IssueSerializer
    queryset = (
        Issue.objects.select_related('project', 'created_by', 'updated_by', 'closed_by')
        .annotate(
            rule_count=Count('rules', distinct=True),
            active_rule_count=Count('rules', filter=Q(rules__active=True), distinct=True),
            result_count=Count('rules__rule_results', distinct=True),
            has_any_rule=Exists(any_rule_subquery()),
            has_active_rule=Exists(active_rule_subquery()),
        )
        .prefetch_related(active_rules_prefetch())
        .order_by('-created_at')
    )
    http_method_names: typing.ClassVar[list] = [
        'get',
        'post',
        'patch',
        'delete',
        'head',
        'options',
    ]
    filterset_class = IssueFilterSet
    filter_backends: typing.ClassVar[list] = [DjangoFilterBackend, StableOrderingFilter]
    ordering_fields: typing.ClassVar[list] = ['created_at', 'updated_at', 'title', 'state']
    ordering: typing.ClassVar[list] = ['-created_at']

    @action(detail=False, methods=['get'])
    def facets(self, request, *args, **kwargs):
        return _facets(
            request,
            IssueFilterSet,
            self.get_queryset(),
            _ISSUE_FACET_PARAMS,
            _issue_facet_counts,
        )

    @check_action_permission('manage_issues')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=_actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_issues')
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=_actor(request))
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issues([instance.id]),
        )
        return Response(serializer.data)

    @check_action_permission('manage_issues')
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        run_ids = ClassificationService.runs_for_issues([instance.id])
        instance.delete()
        RunCache.invalidate_classification_affected(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'])
    @check_action_permission('manage_issues')
    def close(self, request, *args, **kwargs):
        ids = _action_ids(request, 'issue')
        rows = list(Issue.objects.filter(id__in=ids).values_list('id', 'state'))
        existing_ids = {issue_id for issue_id, _ in rows}
        updated_ids = [issue_id for issue_id, state in rows if state == IssueState.OPEN]

        actor = _actor(request)
        now = timezone.now()
        with transaction.atomic():
            Issue.objects.filter(id__in=updated_ids).update(
                state=IssueState.CLOSED,
                closed_by=actor,
                closed_at=now,
            )
            IssueRule.objects.filter(issue_id__in=updated_ids, active=True).update(
                active=False,
                deactivated_by=actor,
                deactivated_at=now,
            )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issues(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))

    @action(detail=False, methods=['post'])
    @check_action_permission('manage_issues')
    def reopen(self, request, *args, **kwargs):
        ids = _action_ids(request, 'issue')
        rows = list(Issue.objects.filter(id__in=ids).values_list('id', 'state'))
        existing_ids = {issue_id for issue_id, _ in rows}
        updated_ids = [issue_id for issue_id, state in rows if state == IssueState.CLOSED]

        Issue.objects.filter(id__in=updated_ids).update(
            state=IssueState.OPEN,
            closed_by=None,
            closed_at=None,
        )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issues(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))


_RULE_FACET_PARAMS = {
    'active': 'active',
    'category': 'category',
    'expected': 'expected',
    'project': 'project',
    'issue_state': 'issue_state',
    'tag': 'tag',
    'verdict': 'verdict',
    'parameter': 'parameter',
}

_MATCHER_FIELD_BY_FACET = {
    'tag': 'tags',
    'verdict': 'verdicts',
    'parameter': 'parameters',
}


def _matcher_value_counts(qs, field):
    """
    {value: rules carrying it} over a JSON matcher field, most common first.
    Parameters are spelled 'name=value', the form the `parameter` filter takes.
    """
    counts = Counter()
    for captured in qs.values_list(field, flat=True):
        if field == 'parameters':
            delim = settings.KEY_VALUE_DELIMITER
            captured = [f'{name}{delim}{value}' for name, value in (captured or {}).items()]
        counts.update(set(captured or []))
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _rule_facet_counts(dim_key, qs):
    if dim_key == 'active':
        counts = _grouped_counts(qs, 'active')
        return {'true': counts.get(True, 0), 'false': counts.get(False, 0)}
    if dim_key == 'category':
        counts = _grouped_counts(qs, 'category')
        return {category: counts.get(category, 0) for category in IssueCategory.values}
    if dim_key == 'expected':
        return {
            disposition: qs.filter(disposition_query([disposition])).count()
            for disposition in DISPOSITION_TO_EXPECTED
        }
    if dim_key == 'issue_state':
        counts = _grouped_counts(qs, 'issue__state')
        return {state: counts.get(state, 0) for state in IssueState.values}
    if dim_key in _MATCHER_FIELD_BY_FACET:
        return _matcher_value_counts(qs, _MATCHER_FIELD_BY_FACET[dim_key])
    return _grouped_counts(qs, 'issue__project_id')


@issue_rule_viewset_schema
class IssueRuleViewSet(ModelViewSet):
    serializer_class = IssueRuleSerializer
    queryset = (
        IssueRule.objects.select_related('issue__project', 'test')
        .annotate(test_name=F('test__name'), issue_title=F('issue__title'))
        .order_by('-created_at')
    )
    http_method_names: typing.ClassVar[list] = [
        'get',
        'post',
        'patch',
        'delete',
        'head',
        'options',
    ]
    filterset_class = IssueRuleFilterSet
    filter_backends: typing.ClassVar[list] = [DjangoFilterBackend, StableOrderingFilter]
    ordering_fields: typing.ClassVar[list] = [
        'created_at',
        'category',
        'active',
        'test_name',
        'issue_title',
    ]
    ordering: typing.ClassVar[list] = ['-created_at']

    @action(detail=False, methods=['get'])
    def facets(self, request, *args, **kwargs):
        return _facets(
            request,
            IssueRuleFilterSet,
            self.get_queryset(),
            _RULE_FACET_PARAMS,
            _rule_facet_counts,
        )

    @check_action_permission('manage_issues')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=_actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_issues')
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=_actor(request))
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rules([instance.id]),
        )
        return Response(serializer.data)

    @check_action_permission('manage_issues')
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        run_ids = ClassificationService.runs_for_rules([instance.id])
        instance.delete()
        RunCache.invalidate_classification_affected(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'])
    @check_action_permission('manage_issues')
    def activate(self, request, *args, **kwargs):
        ids = _action_ids(request, 'issue rule')
        rows = list(IssueRule.objects.filter(id__in=ids).values_list('id', 'active'))
        existing_ids = {rule_id for rule_id, _ in rows}
        updated_ids = [rule_id for rule_id, active in rows if not active]

        IssueRule.objects.filter(id__in=updated_ids).update(
            active=True,
            deactivated_by=None,
            deactivated_at=None,
        )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rules(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))

    @action(detail=False, methods=['post'])
    @check_action_permission('manage_issues')
    def deactivate(self, request, *args, **kwargs):
        ids = _action_ids(request, 'issue rule')
        rows = list(IssueRule.objects.filter(id__in=ids).values_list('id', 'active'))
        existing_ids = {rule_id for rule_id, _ in rows}
        updated_ids = [rule_id for rule_id, active in rows if active]

        actor = _actor(request)
        now = timezone.now()
        IssueRule.objects.filter(id__in=updated_ids).update(
            active=False,
            deactivated_by=actor,
            deactivated_at=now,
        )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rules(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))


@issue_picker_viewset_schema
class IssuePickerViewSet(GenericViewSet):
    filter_backends: typing.ClassVar[list] = []
    renderer_classes: typing.ClassVar[list] = [JSONRenderer]
    serializer_class = IssuePickerOptionSerializer

    def list(self, request, *args, **kwargs):
        project_id = request.query_params.get('project')
        search = (request.query_params.get('search') or '').strip()
        state = request.query_params.get('state')
        test_param = (request.query_params.get('test') or '').strip()

        issues_qs = Issue.objects.all()
        if project_id:
            issues_qs = issues_qs.filter(project_id=project_id)
        if state:
            issues_qs = issues_qs.filter(state=state)
        if test_param:
            test_ids = (
                [int(test_param)] if test_param.isdigit() else get_test_ids_by_name(test_param)
            )
            issues_qs = issues_qs.filter(rules__test_id__in=test_ids).distinct()

        if search:
            issues = list(
                issues_qs.filter(
                    Q(title__icontains=search) | Q(bug_key__icontains=search),
                )
                .prefetch_related(active_rules_prefetch())
                .order_by('title')[:20],
            )
        else:
            recent = (
                RuleResult.objects.filter(issue_rule__issue__in=issues_qs)
                .values('issue_rule__issue_id')
                .annotate(last_used=Max('created_at'))
                .order_by('-last_used')[:10]
            )
            ids = [row['issue_rule__issue_id'] for row in recent]
            by_id = (
                Issue.objects.filter(id__in=ids)
                .prefetch_related(active_rules_prefetch())
                .in_bulk()
            )
            issues = [by_id[i] for i in ids if i in by_id]

        data = [
            {
                'id': issue.id,
                'title': issue.title,
                'bug_key': issue.bug_key,
                'state': issue.state,
                'bug_url': issue.bug_url,
                'rules': [
                    {
                        'rule_id': rule.id,
                        'category': rule.category,
                        'expected': rule.expected,
                        'effect': effect_for(rule.is_suppressed, rule.expected, issue.state),
                    }
                    for rule in issue._active_rules_cache
                ],
            }
            for issue in issues
        ]

        serializer = self.get_serializer(data, many=True)
        return Response(serializer.data)


@test_picker_viewset_schema
class TestPickerViewSet(GenericViewSet):
    """
    GET /tests/picker/?project=&search= - compact test options for the rule
    form: up to 20 path matches for the given search text, or the 10 most
    recently ruled tests when no search text is given. Only tests that have
    results in the given project.

    A test's `name` alone does not identify it - the same name can occur
    under different parent packages with a different id each time - so
    `search` matches the full `path`, not `name`.
    """

    filter_backends: typing.ClassVar[list] = []
    renderer_classes: typing.ClassVar[list] = [JSONRenderer]
    serializer_class = TestPickerOptionSerializer

    def list(self, request, *args, **kwargs):
        project_id = request.query_params.get('project')
        search = (request.query_params.get('search') or '').strip()

        results_qs = TestIterationResult.objects.all()
        if project_id:
            results_qs = results_qs.filter(project_id=project_id)
        node_ids = results_qs.values_list('iteration__test_id', flat=True).distinct()

        test_entity = ResultType.conv('test')
        tested_ids = list(
            Test.objects.filter(id__in=node_ids, result_type=test_entity).values_list(
                'id',
                flat=True,
            ),
        )

        if search:
            paths_by_id = build_test_paths(tested_ids)
            matches = sorted(
                (
                    (test_id, path)
                    for test_id, path in paths_by_id.items()
                    if search.lower() in path.lower()
                ),
                key=lambda item: item[1],
            )[:20]
            ids = [test_id for test_id, _ in matches]
            paths_by_id = dict(matches)
        else:
            recent = (
                IssueRule.objects.filter(test_id__in=tested_ids)
                .values('test_id')
                .annotate(last_used=Max('created_at'))
                .order_by('-last_used')[:10]
            )
            ids = [row['test_id'] for row in recent]
            paths_by_id = build_test_paths(ids)

        tests_by_id = Test.objects.filter(id__in=ids).in_bulk()
        data = [
            {
                'id': test_id,
                'name': tests_by_id[test_id].name,
                'path': paths_by_id.get(test_id, ''),
            }
            for test_id in ids
            if test_id in tests_by_id
        ]

        serializer = self.get_serializer(data, many=True)
        return Response(serializer.data)
