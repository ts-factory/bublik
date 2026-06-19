# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import typing

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import check_action_permission, get_user_by_access_token
from bublik.core.run.classification import (
    invalidate_run_stats,
    runs_for_issue,
    runs_for_rule,
)
from bublik.data.models import Issue, IssueRule, IssueState
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer


def _actor(request):
    return get_user_by_access_token(request.COOKIES.get('access_token'))


class IssueViewSet(ModelViewSet):
    serializer_class = IssueSerializer
    queryset = Issue.objects.all().order_by('-created_at')
    # Disable the global auto FilterSet; queryset filtering is done explicitly below.
    filter_backends: typing.ClassVar[list] = []

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('state'):
            qs = qs.filter(state=params['state'])
        if params.get('category'):
            qs = qs.filter(rules__category=params['category']).distinct()
        # "Issues in project X" = referenced by that project's rules.
        if params.get('project'):
            qs = qs.filter(rules__project_id=params['project']).distinct()
        return qs

    @check_action_permission('manage_classifications')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=_actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @check_action_permission('manage_classifications')
    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=_actor(request))
        invalidate_run_stats(runs_for_issue(instance))
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_classifications')
    def close(self, request, *args, **kwargs):
        issue = self.get_object()
        issue.state = IssueState.CLOSED
        issue.closed_by = _actor(request)
        issue.closed_at = timezone.now()
        issue.save()
        # Auto-deactivate the issue's rules.
        for rule in issue.rules.filter(active=True):
            rule.active = False
            rule.deactivated_by = issue.closed_by
            rule.deactivated_at = issue.closed_at
            rule.save()
        invalidate_run_stats(runs_for_issue(issue))
        return Response(IssueSerializer(issue).data)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_classifications')
    def reopen(self, request, *args, **kwargs):
        issue = self.get_object()
        issue.state = IssueState.OPEN
        issue.closed_by = None
        issue.closed_at = None
        issue.save()  # rules stay deactivated per spec
        invalidate_run_stats(runs_for_issue(issue))
        return Response(IssueSerializer(issue).data)


class IssueRuleViewSet(ModelViewSet):
    serializer_class = IssueRuleSerializer
    queryset = IssueRule.objects.all().order_by('-created_at')
    # JSONField (parameters) breaks the global auto FilterSet; filter explicitly below.
    filter_backends: typing.ClassVar[list] = []

    def get_queryset(self):
        qs = super().get_queryset()
        params = self.request.query_params
        if params.get('project'):
            qs = qs.filter(project_id=params['project'])
        if params.get('issue'):
            qs = qs.filter(issue_id=params['issue'])
        return qs

    @check_action_permission('manage_classifications')
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=_actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_classifications')
    def deactivate(self, request, *args, **kwargs):
        rule = self.get_object()
        rule.active = False
        rule.deactivated_by = _actor(request)
        rule.deactivated_at = timezone.now()
        rule.save()
        invalidate_run_stats(runs_for_rule(rule))
        return Response(IssueRuleSerializer(rule).data)

    @action(detail=True, methods=['post'])
    @check_action_permission('manage_classifications')
    def activate(self, request, *args, **kwargs):
        rule = self.get_object()
        rule.active = True
        rule.deactivated_by = None
        rule.deactivated_at = None
        rule.save()
        invalidate_run_stats(runs_for_rule(rule))
        return Response(IssueRuleSerializer(rule).data)
