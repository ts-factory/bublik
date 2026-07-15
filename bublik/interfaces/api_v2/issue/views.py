# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import typing

from django.conf import settings
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import check_action_permission, get_user_by_access_token
from bublik.core.cache import RunCache
from bublik.core.datetime_formatting import parse_date_param
from bublik.core.run.classification import ClassificationService
from bublik.data.models import Issue, IssueRule, IssueState
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
from bublik.interfaces.api_v2.issue.schemas import (
    issue_rule_viewset_schema,
    issue_viewset_schema,
)
from bublik.interfaces.api_v2.issue.serializers import ActionResultSerializer


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


@issue_viewset_schema
class IssueViewSet(ModelViewSet):
    serializer_class = IssueSerializer
    queryset = (
        Issue.objects.annotate(
            rule_count=Count('rules', distinct=True),
            active_rule_count=Count('rules', filter=Q(rules__active=True), distinct=True),
        )
        .prefetch_related(
            Prefetch(
                'rules',
                queryset=IssueRule.objects.filter(active=True),
                to_attr='_active_rules_cache',
            ),
        )
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
    filter_backends: typing.ClassVar[list] = [OrderingFilter]
    ordering_fields: typing.ClassVar[list] = ['created_at', 'updated_at', 'title', 'state']
    ordering: typing.ClassVar[list] = ['-created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('state'):
            qs = qs.filter(state__in=params['state'].split(settings.QUERY_DELIMITER))
        if params.get('project'):
            qs = qs.filter(project_id=params['project'])
        if params.get('category'):
            qs = qs.filter(
                pk__in=Issue.objects.filter(
                    rules__category=params['category'],
                    rules__active=True,
                ).values('pk'),
            )
        if params.get('search'):
            search = params['search']
            query = (
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(bug_key__icontains=search)
            )
            if search.startswith('#') and search[1:].isdigit():
                query |= Q(id=int(search[1:]))
            qs = qs.filter(query)
        if params.get('created_after'):
            date = parse_date_param(params['created_after'], 'created_after')
            qs = qs.filter(created_at__date__gte=date)
        if params.get('created_before'):
            date = parse_date_param(params['created_before'], 'created_before')
            qs = qs.filter(created_at__date__lte=date)
        if params.get('updated_after'):
            date = parse_date_param(params['updated_after'], 'updated_after')
            qs = qs.filter(updated_at__date__gte=date)
        if params.get('updated_before'):
            date = parse_date_param(params['updated_before'], 'updated_before')
            qs = qs.filter(updated_at__date__lte=date)

        return qs

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


@issue_rule_viewset_schema
class IssueRuleViewSet(ModelViewSet):
    serializer_class = IssueRuleSerializer
    queryset = (
        IssueRule.objects.select_related('issue')
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
    filter_backends: typing.ClassVar[list] = [OrderingFilter]
    ordering_fields: typing.ClassVar[list] = [
        'created_at',
        'category',
        'active',
        'test_name',
        'issue_title',
    ]
    ordering: typing.ClassVar[list] = ['-created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('project'):
            qs = qs.filter(issue__project_id=params['project'])
        if params.get('issue'):
            qs = qs.filter(issue_id=params['issue'])
        if params.get('search'):
            search = params['search']
            qs = qs.filter(
                Q(test__name__icontains=search)
                | Q(issue__title__icontains=search)
                | Q(issue__bug_key__icontains=search),
            )
        if params.get('category'):
            qs = qs.filter(category=params['category'])
        if params.get('active'):
            bool_values = {'true': True, 'false': False}
            values = params['active'].split(settings.QUERY_DELIMITER)
            if any(v.lower() not in bool_values for v in values):
                raise ValidationError({'active': "Expected 'true' and/or 'false'."})
            qs = qs.filter(active__in=[bool_values[v.lower()] for v in values])
        if params.get('expected'):
            expected = params['expected']
            if expected == 'expected':
                qs = qs.filter(expected=True)
            elif expected == 'unexpected':
                qs = qs.filter(expected=False)
            elif expected == 'none':
                qs = qs.filter(expected__isnull=True)
            else:
                msg = "Expected 'expected', 'unexpected', or 'none'."
                raise ValidationError({'expected': msg})
        if params.get('created_after'):
            date = parse_date_param(params['created_after'], 'created_after')
            qs = qs.filter(created_at__date__gte=date)
        if params.get('created_before'):
            date = parse_date_param(params['created_before'], 'created_before')
            qs = qs.filter(created_at__date__lte=date)
        return qs

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
