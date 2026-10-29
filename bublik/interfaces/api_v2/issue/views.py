# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from collections import Counter
from dataclasses import asdict
import json
import typing

from django.conf import settings
from django.db.models import Count, Exists, Max, OuterRef, Q
from django_filters.rest_framework import DjangoFilterBackend
from django_filters.utils import translate_validation
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from bublik.core.auth import check_action_permission_for_projects, get_request_user
from bublik.core.classification import (
    DISPOSITION_TO_EXPECTED,
    RulesState,
    active_rules_prefetch,
    disposition_query,
    effect_for,
    rules_state_query,
)
from bublik.core.filter_backends import StableOrderingFilter
from bublik.core.issue import (
    IssueManagementService,
    IssueRuleManagementService,
    issues_queryset,
    issues_with_rules_state,
    rules_queryset,
)
from bublik.core.issue_results import IssueResultsFilters, IssueResultsService
from bublik.core.pagination import PerProjectPageNumberPagination
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
    BulkIdsRequestSerializer,
    IssuePickerOptionSerializer,
    IssueResultsFacetsSerializer,
    IssueResultsQuerySerializer,
    IssueResultsResponseSerializer,
    TestPickerOptionSerializer,
)


def _actor(request):
    return get_request_user(request)


def _action_ids(request):
    """Validated IDs from a bulk action's body, without duplicates."""
    serializer = BulkIdsRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return list(dict.fromkeys(serializer.validated_data['ids']))


def _action_response(outcome):
    return Response(ActionResultSerializer(asdict(outcome)).data)


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


class IssueRulePagination(PerProjectPageNumberPagination):
    project_field = 'issue__project_id'


@issue_viewset_schema
class IssueViewSet(ModelViewSet):
    serializer_class = IssueSerializer
    queryset = issues_queryset()
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
    pagination_class = PerProjectPageNumberPagination
    ordering_fields: typing.ClassVar[list] = [
        'created_at',
        'updated_at',
        'title',
        'state',
        'result_count',
    ]
    ordering: typing.ClassVar[list] = ['-created_at']

    @action(detail=False, methods=['get'])
    def facets(self, request, *args, **kwargs):
        # Over the bare issues: the counters of the list aren't counted here,
        # and joining them in would make every facet scan all the stamps.
        return _facets(
            request,
            IssueFilterSet,
            issues_with_rules_state(),
            _ISSUE_FACET_PARAMS,
            _issue_facet_counts,
        )

    @action(detail=True, methods=['get'], pagination_class=None)
    def results(self, request, pk=None):
        issue = get_object_or_404(Issue, pk=pk)
        query = IssueResultsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        result_properties = params.get('result_properties')

        page = IssueResultsService.page(
            issue.id,
            IssueResultsFilters(
                results=params.get('results'),
                result_properties=(
                    settings.QUERY_DELIMITER.join(result_properties)
                    if result_properties
                    else None
                ),
                requirements=params.get('requirements'),
                categories=params.get('category', []),
                classifications=params.get('classification', []),
                verdicts=params.get('verdict', []),
                artifacts=params.get('artifact', []),
                parameters=params.get('parameter', []),
            ),
            page=params.get('page'),
            page_size=params.get('page_size'),
        )
        return Response(IssueResultsResponseSerializer(page).data)

    @action(detail=True, methods=['get'], url_path='results/facets', pagination_class=None)
    def results_facets(self, request, pk=None):
        issue = get_object_or_404(Issue, pk=pk)
        facets = IssueResultsService.facets(issue.id)
        return Response(IssueResultsFacetsSerializer(facets).data)

    def create(self, request, *args, **kwargs):
        serializer = IssueManagementService.validate(request.data)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [serializer.validated_data['project'].id],
        )
        IssueManagementService.create(serializer, _actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = IssueManagementService.validate(request.data, instance)
        # The issue may be moved to another project, both must allow it.
        new_project = serializer.validated_data.get('project')
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [instance.project_id, new_project.id if new_project else instance.project_id],
        )
        IssueManagementService.update(serializer, _actor(request))
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        check_action_permission_for_projects(request, 'manage_issues', [instance.project_id])
        IssueManagementService.delete(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'])
    def close(self, request, *args, **kwargs):
        ids = _action_ids(request)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            IssueManagementService.projects_of(ids),
        )
        return _action_response(IssueManagementService.close(ids, _actor(request)))

    @action(detail=False, methods=['post'])
    def reopen(self, request, *args, **kwargs):
        ids = _action_ids(request)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            IssueManagementService.projects_of(ids),
        )
        return _action_response(IssueManagementService.reopen(ids, _actor(request)))


_RULE_FACET_PARAMS = {
    'active': 'active',
    'category': 'category',
    'expected': 'expected',
    'project': 'project',
    'issue_state': 'issue_state',
    'tag': 'tag',
    'verdicts': 'verdicts',
    'parameter': 'parameter',
}

_MATCHER_FIELD_BY_FACET = {
    'tag': 'tags',
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


def _verdicts_counts(qs):
    """
    {verdicts: rules having exactly them}, most common first. The verdicts
    are spelled as JSON, the form the `verdicts` filter takes: null for the
    rules ignoring them, [] for those of the results without verdicts.
    """
    counts = Counter(
        json.dumps(verdicts, ensure_ascii=False)
        for verdicts in qs.values_list('verdicts', flat=True)
    )
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
    if dim_key == 'verdicts':
        return _verdicts_counts(qs)
    if dim_key in _MATCHER_FIELD_BY_FACET:
        return _matcher_value_counts(qs, _MATCHER_FIELD_BY_FACET[dim_key])
    return _grouped_counts(qs, 'issue__project_id')


@issue_rule_viewset_schema
class IssueRuleViewSet(ModelViewSet):
    serializer_class = IssueRuleSerializer
    queryset = rules_queryset()
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
    pagination_class = IssueRulePagination
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
        serializer = IssueRuleManagementService.validate(request.data)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [serializer.validated_data['issue'].project_id],
        )
        IssueRuleManagementService.create(serializer, _actor(request))
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = IssueRuleManagementService.validate(request.data, instance)
        # The rule may be moved to an issue of another project, both must allow it.
        new_issue = serializer.validated_data.get('issue')
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [instance.issue.project_id, (new_issue or instance.issue).project_id],
        )
        IssueRuleManagementService.update(serializer, _actor(request))
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [instance.issue.project_id],
        )
        IssueRuleManagementService.delete(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'])
    def activate(self, request, *args, **kwargs):
        ids = _action_ids(request)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            IssueRuleManagementService.projects_of(ids),
        )
        return _action_response(IssueRuleManagementService.activate(ids, _actor(request)))

    @action(detail=False, methods=['post'])
    def deactivate(self, request, *args, **kwargs):
        ids = _action_ids(request)
        check_action_permission_for_projects(
            request,
            'manage_issues',
            IssueRuleManagementService.projects_of(ids),
        )
        return _action_response(IssueRuleManagementService.deactivate(ids, _actor(request)))


@issue_picker_viewset_schema
class IssuePickerViewSet(GenericViewSet):
    filter_backends: typing.ClassVar[list] = []
    pagination_class = None
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
            states = state.split(settings.QUERY_DELIMITER)
            if any(s not in IssueState.values for s in states):
                msg = f'Expected any of: {", ".join(IssueState.values)}.'
                raise ValidationError({'state': msg})
            issues_qs = issues_qs.filter(state__in=states)
        if test_param:
            test_ids = (
                [int(test_param)]
                if test_param.isascii() and test_param.isdecimal()
                else get_test_ids_by_name(test_param)
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
    pagination_class = None
    renderer_classes: typing.ClassVar[list] = [JSONRenderer]
    serializer_class = TestPickerOptionSerializer

    def list(self, request, *args, **kwargs):
        project_id = request.query_params.get('project')
        search = (request.query_params.get('search') or '').strip()

        # Checked test by test rather than by collecting the tests of every
        # result: a test's results are found by index, while the results of
        # a project are far more numerous than its tests.
        results_qs = TestIterationResult.objects.filter(iteration__test_id=OuterRef('pk'))
        if project_id:
            results_qs = results_qs.filter(project_id=project_id)
        tested_ids = list(
            Test.objects.filter(result_type=ResultType.conv('test'))
            .filter(Exists(results_qs))
            .values_list('id', flat=True),
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
