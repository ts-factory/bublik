# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from collections import Counter
import typing

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from django_filters.utils import translate_validation
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import check_action_permission_for_projects, get_user_by_access_token
from bublik.core.cache import RunCache
from bublik.core.classification import (
    DISPOSITION_TO_EXPECTED,
    RulesState,
    active_rule_subquery,
    active_rules_prefetch,
    any_rule_subquery,
    disposition_query,
    rules_state_query,
)
from bublik.core.filter_backends import StableOrderingFilter
from bublik.core.run.classification import ClassificationService
from bublik.data.models import Issue, IssueCategory, IssueRule, IssueState, RuleResult
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
from bublik.interfaces.api_v2.issue.filters import IssueFilterSet, IssueRuleFilterSet
from bublik.interfaces.api_v2.issue.schemas import (
    issue_rule_viewset_schema,
    issue_viewset_schema,
)
from bublik.interfaces.api_v2.issue.serializers import (
    ActionResultSerializer,
    BulkIdsRequestSerializer,
)


def _actor(request):
    return get_user_by_access_token(request.COOKIES.get('access_token'))


def _action_ids(request):
    """Validated IDs from a bulk action's body, without duplicates."""
    serializer = BulkIdsRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return list(dict.fromkeys(serializer.validated_data['ids']))


def _action_summary(ids, existing_ids, updated_ids):
    """The IDs of a bulk action split by outcome, each in the order requested."""
    updated_ids = set(updated_ids)
    data = {
        'requested': len(ids),
        'updated': [i for i in ids if i in updated_ids],
        'unchanged': [i for i in ids if i in existing_ids and i not in updated_ids],
        'not_found': [i for i in ids if i not in existing_ids],
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
    # Validated in full first, as the list is: a malformed value is a 400,
    # not a filter silently left out.
    filterset = filterset_class(data=request.query_params, queryset=base_qs, request=request)
    if not filterset.is_valid():
        raise translate_validation(filterset.errors)
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


def _per_issue_count(queryset, issue_field, count_field):
    """
    The number of distinct `count_field` values of `queryset` for the outer
    issue, as a scalar subquery: it is computed only for the issues a page
    returns, not by joining every rule and stamp of every filtered issue
    before the page is cut.
    """
    return Coalesce(
        Subquery(
            queryset.order_by()
            .values(issue_field)
            .annotate(count=Count(count_field, distinct=True))
            .values('count'),
            output_field=IntegerField(),
        ),
        0,
    )


def _issues_with_rules_state():
    """Issues annotated with what the `rules` filter and rules_state read."""
    return Issue.objects.annotate(
        has_any_rule=Exists(any_rule_subquery()),
        has_active_rule=Exists(active_rule_subquery()),
    )


@issue_viewset_schema
class IssueViewSet(ModelViewSet):
    serializer_class = IssueSerializer
    queryset = (
        _issues_with_rules_state()
        .select_related('project', 'created_by', 'updated_by', 'closed_by')
        .annotate(
            rule_count=_per_issue_count(
                IssueRule.objects.filter(issue=OuterRef('pk')),
                'issue',
                'id',
            ),
            active_rule_count=_per_issue_count(
                IssueRule.objects.filter(issue=OuterRef('pk'), active=True),
                'issue',
                'id',
            ),
            result_count=_per_issue_count(
                RuleResult.objects.filter(issue_rule__issue=OuterRef('pk')),
                'issue_rule__issue',
                'result',
            ),
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
        # Over the bare issues: the counters of the list aren't counted here,
        # and joining them in would make every facet scan all the stamps.
        return _facets(
            request,
            IssueFilterSet,
            _issues_with_rules_state(),
            _ISSUE_FACET_PARAMS,
            _issue_facet_counts,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [serializer.validated_data['project'].id],
        )
        serializer.save(created_by=_actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # The issue may be moved to another project, both must allow it.
        new_project = serializer.validated_data.get('project')
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [instance.project_id, new_project.id if new_project else instance.project_id],
        )
        serializer.save(updated_by=_actor(request))
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issues([instance.id]),
        )
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        check_action_permission_for_projects(request, 'manage_issues', [instance.project_id])
        run_ids = ClassificationService.runs_for_issues([instance.id])
        instance.delete()
        RunCache.invalidate_classification_affected(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'])
    def close(self, request, *args, **kwargs):
        ids = _action_ids(request)
        rows = list(Issue.objects.filter(id__in=ids).values_list('id', 'state', 'project_id'))
        check_action_permission_for_projects(request, 'manage_issues', [row[2] for row in rows])
        existing_ids = {issue_id for issue_id, _, _ in rows}
        updated_ids = [issue_id for issue_id, state, _ in rows if state == IssueState.OPEN]

        actor = _actor(request)
        now = timezone.now()
        with transaction.atomic():
            Issue.objects.filter(id__in=updated_ids).update(
                state=IssueState.CLOSED,
                closed_by=actor,
                closed_at=now,
                updated_by=actor,
                updated_at=now,
            )
            IssueRule.objects.filter(issue_id__in=updated_ids, active=True).update(
                active=False,
                deactivated_by=actor,
                deactivated_at=now,
                updated_by=actor,
                updated_at=now,
            )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issues(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))

    @action(detail=False, methods=['post'])
    def reopen(self, request, *args, **kwargs):
        ids = _action_ids(request)
        rows = list(Issue.objects.filter(id__in=ids).values_list('id', 'state', 'project_id'))
        check_action_permission_for_projects(request, 'manage_issues', [row[2] for row in rows])
        existing_ids = {issue_id for issue_id, _, _ in rows}
        updated_ids = [issue_id for issue_id, state, _ in rows if state == IssueState.CLOSED]

        Issue.objects.filter(id__in=updated_ids).update(
            state=IssueState.OPEN,
            closed_by=None,
            closed_at=None,
            updated_by=_actor(request),
            updated_at=timezone.now(),
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

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [serializer.validated_data['issue'].project_id],
        )
        serializer.save(created_by=_actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        # The rule may be moved to an issue of another project, both must allow it.
        new_issue = serializer.validated_data.get('issue')
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [instance.issue.project_id, (new_issue or instance.issue).project_id],
        )
        serializer.save(updated_by=_actor(request))
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rules([instance.id]),
        )
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [instance.issue.project_id],
        )
        run_ids = ClassificationService.runs_for_rules([instance.id])
        instance.delete()
        RunCache.invalidate_classification_affected(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'])
    def activate(self, request, *args, **kwargs):
        ids = _action_ids(request)
        rows = list(
            IssueRule.objects.filter(id__in=ids).values_list(
                'id',
                'active',
                'issue__state',
                'issue__project_id',
            ),
        )
        check_action_permission_for_projects(request, 'manage_issues', [row[3] for row in rows])
        existing_ids = {rule_id for rule_id, _, _, _ in rows}
        updated_ids = [rule_id for rule_id, active, _, _ in rows if not active]

        # Closing an issue deactivates its rules, so they can't be switched
        # back on until the issue is reopened.
        closed_issue_rule_ids = [
            rule_id
            for rule_id, active, issue_state, _ in rows
            if not active and issue_state == IssueState.CLOSED
        ]
        if closed_issue_rule_ids:
            msg = (
                'Rules of closed issues cannot be activated, '
                f'reopen the issue first: {closed_issue_rule_ids}.'
            )
            raise ValidationError({'ids': msg})

        IssueRule.objects.filter(id__in=updated_ids).update(
            active=True,
            deactivated_by=None,
            deactivated_at=None,
            updated_by=_actor(request),
            updated_at=timezone.now(),
        )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rules(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))

    @action(detail=False, methods=['post'])
    def deactivate(self, request, *args, **kwargs):
        ids = _action_ids(request)
        rows = list(
            IssueRule.objects.filter(id__in=ids).values_list(
                'id',
                'active',
                'issue__project_id',
            ),
        )
        check_action_permission_for_projects(request, 'manage_issues', [row[2] for row in rows])
        existing_ids = {rule_id for rule_id, _, _ in rows}
        updated_ids = [rule_id for rule_id, active, _ in rows if active]

        actor = _actor(request)
        now = timezone.now()
        IssueRule.objects.filter(id__in=updated_ids).update(
            active=False,
            deactivated_by=actor,
            deactivated_at=now,
            updated_by=actor,
            updated_at=now,
        )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rules(updated_ids),
        )
        return Response(_action_summary(ids, existing_ids, updated_ids))
