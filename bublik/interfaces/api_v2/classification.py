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


from rest_framework.viewsets import GenericViewSet

from bublik.core.run.classification import (
    _result_param_dict,
    _result_verdict_set,
)
from bublik.data.models import (
    IssueCategory,
    Project,
    ResultClassification,
    StampOrigin,
    TestIterationResult,
    default_expected_for,
)


class ResultClassifyViewSet(GenericViewSet):
    '''POST /results/<result_id>/classify/?project=<id>

    Body: {issue: <id> | {title, description?, bug_key?},
           category, expected?, scope: 'future'|'oneoff', matcher?: {...}}
    Creates/links an Issue, creates an IssueRule (active iff scope=future),
    and stamps the current result.'''

    # No queryset/model on this viewset; skip the global auto FilterSet.
    filter_backends: typing.ClassVar[list] = []

    @check_action_permission('manage_classifications')
    def create(self, request, *args, **kwargs):
        result = TestIterationResult.objects.select_related('iteration__test').get(
            pk=self.kwargs['result_id'],
        )
        project = Project.objects.get(pk=request.query_params.get('project'))
        actor = _actor(request)
        body = request.data

        issue = self._resolve_issue(body.get('issue'), actor)

        category = body.get('category', IssueCategory.TO_INVESTIGATE)
        expected = body.get('expected')
        if expected is None:
            expected = default_expected_for(category)
        scope = body.get('scope', 'future')
        matcher = body.get('matcher') or {}

        rule = IssueRule.objects.create(
            project=project,
            issue=issue,
            test=result.iteration.test,
            category=category,
            expected=expected,
            active=(scope == 'future'),
            match_parameters=matcher.get('match_parameters', True),
            match_verdicts=matcher.get('match_verdicts', True),
            match_important_tags=matcher.get('match_important_tags', True),
            match_all_tags=matcher.get('match_all_tags', False),
            parameters=matcher.get('parameters', _result_param_dict(result)),
            verdicts=matcher.get('verdicts', sorted(_result_verdict_set(result))),
            tags=matcher.get('tags', self._capture_tags(result)),
            created_by=actor,
        )
        origin = StampOrigin.MANUAL_ONEOFF if scope == 'oneoff' else StampOrigin.MANUAL_APPLY
        ResultClassification.objects.get_or_create(
            result=result, rule=rule, defaults={'origin': origin, 'created_by': actor},
        )
        invalidate_run_stats([result.test_run_id])
        return Response(
            {'issue_id': issue.id, 'rule_id': rule.id},
            status=status.HTTP_201_CREATED,
        )

    def _resolve_issue(self, issue_data, actor):
        if isinstance(issue_data, int) or (
            isinstance(issue_data, str) and str(issue_data).isdigit()
        ):
            return Issue.objects.get(pk=int(issue_data))
        issue_data = issue_data or {}
        issue = Issue.objects.create(
            title=issue_data.get('title', 'Untitled'),
            description=issue_data.get('description'),
            created_by=actor,
        )
        bug_key = issue_data.get('bug_key')
        if bug_key:
            from bublik.data.models import IssueExt
            ext, _ = IssueExt.objects.get_or_create(key=bug_key)
            issue.issue_ext = ext
            issue.save()
        return issue

    def _capture_tags(self, result):
        from bublik.core.run.data import get_tags_by_runs
        important_by_run, _ = get_tags_by_runs([result.test_run])
        return important_by_run.get(result.test_run_id, [])
