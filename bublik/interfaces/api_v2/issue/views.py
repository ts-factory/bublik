# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import typing

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from bublik.core.auth import check_action_permission, get_user_by_access_token
from bublik.core.cache import RunCache
from bublik.core.run.classification import ClassificationService
from bublik.data.models import Issue, IssueRule, IssueState, RuleResult
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
from bublik.interfaces.api_v2.issue.serializers import IssuePickerOptionSerializer


def _actor(request):
    return get_user_by_access_token(request.COOKIES.get('access_token'))


class IssueViewSet(ModelViewSet):
    serializer_class = IssueSerializer
    queryset = Issue.objects.all().order_by('-created_at')
    http_method_names: typing.ClassVar[list] = [
        'get',
        'post',
        'patch',
        'delete',
        'head',
        'options',
    ]
    filter_backends: typing.ClassVar[list] = []

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('state'):
            qs = qs.filter(state=params['state'])
        if params.get('category'):
            qs = qs.filter(rules__category=params['category']).distinct()
        if params.get('project'):
            qs = qs.filter(rules__project_id=params['project']).distinct()
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
            ClassificationService.runs_for_issue(instance),
        )
        return Response(serializer.data)

    @check_action_permission('manage_issues')
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        run_ids = ClassificationService.runs_for_issue(instance)
        instance.delete()
        RunCache.invalidate_classification_affected(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_issues')
    def close(self, request, *args, **kwargs):
        issue = self.get_object()
        with transaction.atomic():
            issue.state = IssueState.CLOSED
            issue.closed_by = _actor(request)
            issue.closed_at = timezone.now()
            issue.save()
            for rule in issue.rules.filter(active=True):
                rule.active = False
                rule.deactivated_by = issue.closed_by
                rule.deactivated_at = issue.closed_at
                rule.save()

        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issue(issue),
        )
        return Response(IssueSerializer(issue).data)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_issues')
    def reopen(self, request, *args, **kwargs):
        issue = self.get_object()
        issue.state = IssueState.OPEN
        issue.closed_by = None
        issue.closed_at = None
        issue.save()
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issue(issue),
        )
        return Response(IssueSerializer(issue).data)


class IssueRuleViewSet(ModelViewSet):
    serializer_class = IssueRuleSerializer
    queryset = IssueRule.objects.all().order_by('-created_at')
    http_method_names: typing.ClassVar[list] = [
        'get',
        'post',
        'patch',
        'delete',
        'head',
        'options',
    ]
    filter_backends: typing.ClassVar[list] = []

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('project'):
            qs = qs.filter(project_id=params['project'])
        if params.get('issue'):
            qs = qs.filter(issue_id=params['issue'])
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
            ClassificationService.runs_for_rule(instance),
        )
        return Response(serializer.data)

    @check_action_permission('manage_issues')
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        run_ids = ClassificationService.runs_for_rule(instance)
        instance.delete()
        RunCache.invalidate_classification_affected(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_issues')
    def deactivate(self, request, *args, **kwargs):
        rule = self.get_object()
        rule.active = False
        rule.deactivated_by = _actor(request)
        rule.deactivated_at = timezone.now()
        rule.save()
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rule(rule),
        )
        return Response(IssueRuleSerializer(rule).data)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_issues')
    def activate(self, request, *args, **kwargs):
        rule = self.get_object()
        rule.active = True
        rule.deactivated_by = None
        rule.deactivated_at = None
        rule.save()
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_rule(rule),
        )
        return Response(IssueRuleSerializer(rule).data)


class IssuePickerViewSet(GenericViewSet):
    filter_backends: typing.ClassVar[list] = []
    renderer_classes: typing.ClassVar[list] = [JSONRenderer]
    serializer_class = IssuePickerOptionSerializer

    def list(self, request, *args, **kwargs):
        project_id = request.query_params.get('project')
        search = (request.query_params.get('search') or '').strip()

        if search:
            issues_qs = Issue.objects.all()
            if project_id:
                issues_qs = issues_qs.filter(rules__project_id=project_id)
            issues = list(
                issues_qs.filter(
                    Q(title__icontains=search) | Q(issue_ext__key__icontains=search)
                )
                .select_related('issue_ext')
                .distinct()
                .order_by('title')[:20],
            )
        else:
            recent_qs = RuleResult.objects.all()
            if project_id:
                recent_qs = recent_qs.filter(issue_rule__project_id=project_id)
            recent = (
                recent_qs.values('issue_rule__issue_id')
                .annotate(last_used=Max('created_at'))
                .order_by('-last_used')[:10]
            )
            ids = [row['issue_rule__issue_id'] for row in recent]
            by_id = Issue.objects.filter(id__in=ids).select_related('issue_ext').in_bulk()
            issues = [by_id[i] for i in ids if i in by_id]

        data = []
        for issue in issues:
            latest_qs = RuleResult.objects.filter(issue_rule__issue=issue)
            if project_id:
                latest_qs = latest_qs.filter(issue_rule__project_id=project_id)
            latest = latest_qs.select_related('issue_rule').order_by('-created_at').first()

            category_qs = issue.rules.all()
            if project_id:
                category_qs = category_qs.filter(project_id=project_id)
            category = (
                latest.issue_rule.category
                if latest
                else category_qs.values_list('category', flat=True).first()
            )
            data.append(
                {
                    'id': issue.id,
                    'title': issue.title,
                    'key': issue.issue_ext.key if issue.issue_ext_id else None,
                    'category': category,
                },
            )

        serializer = self.get_serializer(data, many=True)
        return Response(serializer.data)
