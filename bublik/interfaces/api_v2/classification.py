# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import typing

from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from bublik.core.auth import check_action_permission, get_user_by_access_token
from bublik.core.run.classification import (
    _result_param_dict,
    _result_verdict_set,
    apply_active_rules_manual,
    invalidate_run_stats,
    runs_for_issue,
    runs_for_rule,
)
from bublik.core.run.data import get_tags_by_runs
from bublik.data.models import (
    Issue,
    IssueCategory,
    IssueExt,
    IssueRule,
    IssueState,
    Project,
    ResultClassification,
    StampOrigin,
    TestIterationResult,
    default_expected_for,
)
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer


def _actor(request):
    return get_user_by_access_token(request.COOKIES.get('access_token'))


def _clamp_limit(request, default=20, maximum=100):
    try:
        limit = int(request.query_params.get('limit', default))
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


def _serialize_classified_result(stamp):
    '''Flatten a ResultClassification stamp into a result row.

    Includes the matching rule's category/expected so callers that aggregate
    across rules (per-issue view) can show which rule classified each result.
    '''
    r = stamp.result
    path = []
    node = r.parent_package
    while node is not None:
        if node.iteration_id and node.iteration.test_id:
            path.append(node.iteration.test.name)
        node = node.parent_package
    path.reverse()
    verdicts = list(
        r.meta_results.filter(meta__type='verdict')
        .order_by('serial').values_list('meta__value', flat=True),
    )
    obtained = (
        r.meta_results.filter(meta__type='result')
        .values_list('meta__value', flat=True).first()
    )
    return {
        'result_id': r.id,
        'run_id': r.test_run_id,
        'run_start': r.test_run.start if r.test_run_id else None,
        'name': r.iteration.test.name if r.iteration_id else None,
        'path': path,
        'obtained_result': obtained,
        'verdicts': verdicts,
        'rule_id': stamp.rule_id,
        'category': stamp.rule.category,
        'expected': stamp.rule.expected,
    }


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

    @check_action_permission('manage_classifications')
    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=_actor(request))
        invalidate_run_stats(runs_for_issue(instance))
        return Response(serializer.data)

    @check_action_permission('manage_classifications')
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        run_ids = runs_for_issue(instance)
        instance.delete()
        invalidate_run_stats(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

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

    @action(detail=True, methods=['get'])
    def results(self, request, *args, **kwargs):
        '''All results classified under this issue, across its rules.

        An issue spans multiple rules/tests, so this unions every rule's
        stamps into one newest-run-first list. Scoped to a project when given,
        since rules are per-project.
        '''
        issue = self.get_object()
        limit = _clamp_limit(request)
        stamps = ResultClassification.objects.filter(rule__issue=issue)
        project = request.query_params.get('project')
        if project:
            stamps = stamps.filter(rule__project_id=project)
        # unique(result, rule); a result could carry stamps from two of this
        # issue's rules, so de-dupe by result keeping the newest run.
        stamps = (
            stamps.select_related('rule', 'result__iteration__test', 'result__test_run')
            .order_by('-result__test_run__start', '-result_id')
        )
        rows = []
        seen = set()
        for stamp in stamps:
            if stamp.result_id in seen:
                continue
            seen.add(stamp.result_id)
            rows.append(_serialize_classified_result(stamp))
            if len(rows) >= limit:
                break
        return Response(rows)


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

    @check_action_permission('manage_classifications')
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        serializer.save(updated_by=_actor(request))
        invalidate_run_stats(runs_for_rule(instance))
        return Response(serializer.data)

    @check_action_permission('manage_classifications')
    def partial_update(self, request, *args, **kwargs):
        return self.update(request, *args, partial=True, **kwargs)

    @check_action_permission('manage_classifications')
    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        run_ids = runs_for_rule(instance)
        instance.delete()
        invalidate_run_stats(run_ids)
        return Response(status=status.HTTP_204_NO_CONTENT)

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

    @action(detail=True, methods=['get'])
    def results(self, request, *args, **kwargs):
        rule = self.get_object()
        limit = _clamp_limit(request)
        # unique(result, rule) -> each result appears once for a rule, so no
        # distinct needed; order newest run first.
        stamps = (
            ResultClassification.objects.filter(rule=rule)
            .select_related('rule', 'result__iteration__test', 'result__test_run')
            .order_by('-result__test_run__start', '-result_id')[:limit]
        )
        return Response([_serialize_classified_result(s) for s in stamps])


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
        result = get_object_or_404(
            TestIterationResult.objects.select_related('iteration__test'),
            pk=self.kwargs['result_id'],
        )
        if not request.query_params.get('project'):
            raise ValidationError({'project': 'project query param is required'})
        project = get_object_or_404(Project, pk=request.query_params.get('project'))
        actor = _actor(request)
        body = request.data

        issue = self._resolve_issue(body.get('issue'), actor)

        category = body.get('category', IssueCategory.TO_INVESTIGATE)
        if category not in IssueCategory.values:
            raise ValidationError({'category': f'invalid category: {category!r}'})
        scope = body.get('scope', 'future')
        if scope not in ('future', 'oneoff'):
            raise ValidationError({'scope': f'invalid scope: {scope!r}'})

        # Respect an explicit disposition incl. null (none); only default when
        # the key is absent.
        if 'expected' in body:
            expected = body['expected']
        else:
            expected = default_expected_for(category)
        matcher = body.get('matcher') or {}

        active = scope == 'future'
        origin = StampOrigin.MANUAL_APPLY if active else StampOrigin.MANUAL_ONEOFF

        match_all_tags = matcher.get('match_all_tags', False)
        rule = IssueRule.objects.create(
            project=project,
            issue=issue,
            test=result.iteration.test,
            category=category,
            expected=expected,
            active=active,
            match_parameters=matcher.get('match_parameters', True),
            match_verdicts=matcher.get('match_verdicts', True),
            match_important_tags=matcher.get('match_important_tags', True),
            match_all_tags=match_all_tags,
            parameters=matcher.get('parameters', _result_param_dict(result)),
            verdicts=matcher.get('verdicts', sorted(_result_verdict_set(result))),
            tags=matcher.get('tags', self._capture_tags(result, all_tags=match_all_tags)),
            created_by=actor,
        )
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
            return get_object_or_404(Issue, pk=int(issue_data))
        issue_data = issue_data or {}
        issue = Issue.objects.create(
            title=issue_data.get('title', 'Untitled'),
            description=issue_data.get('description'),
            created_by=actor,
        )
        bug_key = issue_data.get('bug_key')
        if bug_key:
            ext, _ = IssueExt.objects.get_or_create(key=bug_key)
            issue.issue_ext = ext
            issue.save()
        return issue

    def _capture_tags(self, result, all_tags=False):
        # all_tags=True captures important+relevant (the run's full tag set) so a
        # match_all_tags rule gates on every tag; else important tags only.
        if all_tags:
            all_by_run = get_tags_by_runs([result.test_run], not_categorize=True)
            return all_by_run.get(result.test_run_id, [])
        important_by_run, _ = get_tags_by_runs([result.test_run])
        return important_by_run.get(result.test_run_id, [])


class IssuePickerViewSet(GenericViewSet):
    '''GET /issues/picker/?project=<id>&search=<q> - typeahead options for the
    classify popover. No search: top-10 recently-applied issues in the project
    (by latest stamp). With search: title/bug-key matches. Each option carries a
    display tag source (key, else category).'''

    filter_backends: typing.ClassVar[list] = []

    def list(self, request, *args, **kwargs):
        project_id = request.query_params.get('project')
        search = (request.query_params.get('search') or '').strip()

        if search:
            issues = list(
                Issue.objects.filter(rules__project_id=project_id)
                .filter(Q(title__icontains=search) | Q(issue_ext__key__icontains=search))
                .select_related('issue_ext')
                .distinct()
                .order_by('title')[:20],
            )
        else:
            recent = (
                ResultClassification.objects.filter(rule__project_id=project_id)
                .values('rule__issue_id')
                .annotate(last_used=Max('created_at'))
                .order_by('-last_used')[:10]
            )
            ids = [row['rule__issue_id'] for row in recent]
            by_id = Issue.objects.filter(id__in=ids).select_related('issue_ext').in_bulk()
            issues = [by_id[i] for i in ids if i in by_id]

        data = []
        for issue in issues:
            latest = (
                ResultClassification.objects.filter(
                    rule__issue=issue, rule__project_id=project_id,
                )
                .select_related('rule')
                .order_by('-created_at')
                .first()
            )
            category = (
                latest.rule.category
                if latest
                else issue.rules.filter(project_id=project_id)
                .values_list('category', flat=True)
                .first()
            )
            data.append({
                'id': issue.id,
                'title': issue.title,
                'key': issue.issue_ext.key if issue.issue_ext_id else None,
                'category': category,
            })
        return Response(data)


class RunApplyRulesViewSet(GenericViewSet):
    '''POST /runs/<run_id>/apply-rules/?project=<id> - apply active rules to an
    existing run on demand.'''

    # No queryset/model on this viewset; skip the global auto FilterSet.
    filter_backends: typing.ClassVar[list] = []

    @check_action_permission('manage_classifications')
    def create(self, request, *args, **kwargs):
        run = get_object_or_404(TestIterationResult, pk=self.kwargs['run_id'])
        created = apply_active_rules_manual(run)
        invalidate_run_stats([run.id])
        return Response({'stamps_created': created}, status=status.HTTP_200_OK)


class RunIssuesViewSet(GenericViewSet):
    '''GET /runs/<run_id>/issues/ - per-issue summary of classified results in a run.'''

    # Read endpoint; the global AllDjangoFilterBackend breaks on some models.
    filter_backends: typing.ClassVar[list] = []

    def list(self, request, *args, **kwargs):
        run_id = self.kwargs['run_id']
        base = ResultClassification.objects.filter(result__test_run_id=run_id)
        # Distinct result count per issue (a result may be stamped by several rules).
        counts = {
            row['rule__issue_id']: row['c']
            for row in base.values('rule__issue_id').annotate(c=Count('result_id', distinct=True))
        }
        # Issue meta + the (category, expected) pairs seen for it in this run.
        rows: dict = {}
        for m in base.values(
            'rule__issue_id', 'rule__issue__title', 'rule__issue__state',
            'rule__category', 'rule__expected',
        ).distinct():
            iid = m['rule__issue_id']
            r = rows.setdefault(iid, {
                'issue_id': iid,
                'title': m['rule__issue__title'],
                'state': m['rule__issue__state'],
                'result_count': counts.get(iid, 0),
                'categories': [],
            })
            r['categories'].append(
                {'category': m['rule__category'], 'expected': m['rule__expected']},
            )
        # Optional external bug reference per issue.
        keys = dict(
            Issue.objects.filter(id__in=rows.keys())
            .exclude(issue_ext__isnull=True)
            .values_list('id', 'issue_ext__key'),
        )
        for iid, r in rows.items():
            r['bug_key'] = keys.get(iid)
        data = sorted(rows.values(), key=lambda x: (x['title'] or '').lower())
        return Response(data)


class RunIssueResultsViewSet(GenericViewSet):
    '''GET /runs/<run_id>/issues/<issue_id>/results/ - results in a run classified
    under an issue, each with its run-tree package path.'''

    # Read endpoint; the global AllDjangoFilterBackend breaks on some models.
    filter_backends: typing.ClassVar[list] = []

    def list(self, request, *args, **kwargs):
        run_id = self.kwargs['run_id']
        issue_id = self.kwargs['issue_id']
        stamps = (
            ResultClassification.objects.filter(
                result__test_run_id=run_id, rule__issue_id=issue_id,
            )
            .select_related('result__iteration__test')
            .distinct('result_id')
            .order_by('result_id')
        )
        data = []
        for stamp in stamps:
            r = stamp.result
            # Package path (top-down) from the parent_package chain.
            path = []
            node = r.parent_package
            while node is not None:
                if node.iteration_id and node.iteration.test_id:
                    path.append(node.iteration.test.name)
                node = node.parent_package
            path.reverse()
            verdicts = list(
                r.meta_results.filter(meta__type='verdict')
                .order_by('serial').values_list('meta__value', flat=True),
            )
            obtained = (
                r.meta_results.filter(meta__type='result')
                .values_list('meta__value', flat=True).first()
            )
            data.append({
                'result_id': r.id,
                'name': r.iteration.test.name if r.iteration_id else None,
                'path': path,
                'obtained_result': obtained,
                'verdicts': verdicts,
            })
        return Response(data)
