# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from dataclasses import asdict
import typing

from django.conf import settings
from django.db.models import Max, Q
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import get_object_or_404
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ModelViewSet

from bublik.core.auth import check_action_permission_for_projects, get_request_user
from bublik.core.classification import (
    active_rules_prefetch,
    effect_for,
)
from bublik.core.filter_backends import StableOrderingFilter
from bublik.core.issue import (
    IssueManagementService,
    IssueRuleManagementService,
    issues_queryset,
    rules_queryset,
)
from bublik.core.issue.filters import IssueFilterSet, IssueRuleFilterSet
from bublik.core.issue_results import IssueResultsFilters, IssueResultsService
from bublik.core.pagination import PerProjectPageNumberPagination
from bublik.core.run.tests_organization import get_test_ids_by_name
from bublik.data.models import (
    Issue,
    IssueState,
    RuleResult,
)
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
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
    ordering_fields: typing.ClassVar[list] = IssueManagementService.ORDERING_FIELDS
    ordering: typing.ClassVar[list] = ['-created_at']

    @action(detail=False, methods=['get'])
    def facets(self, request, *args, **kwargs):
        return Response(asdict(IssueManagementService.facets(request.query_params)))

    @action(detail=True, methods=['get'], pagination_class=None)
    def results(self, request, pk=None):
        issue = get_object_or_404(Issue, pk=pk)
        query = IssueResultsQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        page = IssueResultsService.page(
            issue.id,
            IssueResultsFilters.from_params(params),
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
    ordering_fields: typing.ClassVar[list] = IssueRuleManagementService.ORDERING_FIELDS
    ordering: typing.ClassVar[list] = ['-created_at']

    @action(detail=False, methods=['get'])
    def facets(self, request, *args, **kwargs):
        return Response(asdict(IssueRuleManagementService.facets(request.query_params)))

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

        data = [
            asdict(option)
            for option in IssueRuleManagementService.test_options(project_id, search)
        ]

        serializer = self.get_serializer(data, many=True)
        return Response(serializer.data)
