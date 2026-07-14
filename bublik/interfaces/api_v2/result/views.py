# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import check_action_permission, get_user_by_access_token
from bublik.core.cache import RunCache
from bublik.core.result import ResultService
from bublik.core.run.classification import ClassificationService
from bublik.core.run.data import get_tags_by_runs
from bublik.core.run.stats import (
    generate_results_details,
)
from bublik.data.models import (
    Issue,
    RuleResult,
    RuleResultOrigin,
)
from bublik.data.serializers import (
    IssueRuleSerializer,
    IssueSerializer,
    TestIterationResultSerializer,
)
from bublik.interfaces.api_v2.result.schemas import result_viewset_schema
from bublik.interfaces.api_v2.result.serializers import (
    ClassifyRequestSerializer,
    ClassifyResponseSerializer,
)


__all__ = [
    'ResultViewSet',
]


@result_viewset_schema
class ResultViewSet(ModelViewSet):
    serializer_class = TestIterationResultSerializer
    filter_backends: ClassVar[list] = []

    def get_queryset(self):
        parent_id = self.request.query_params.get('parent_id')
        test_name = self.request.query_params.get('test_name')
        start_exec_seqno = self.request.query_params.get('start_exec_seqno')
        results = self.request.query_params.get('results')
        result_properties = self.request.query_params.get('result_properties')
        requirements = self.request.query_params.get('requirements')

        return ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
            start_exec_seqno=start_exec_seqno,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
        )

    def retrieve(self, request, pk=None):
        return Response(data={'result': ResultService.get_result_details(pk)})

    def list(self, request):
        return Response(
            data={'results': generate_results_details(self.get_queryset())},
        )

    @action(detail=True, methods=['get'])
    def artifacts_and_verdicts(self, request, pk=None):
        return Response(ResultService.get_result_artifacts_and_verdicts(pk))

    @action(detail=True, methods=['get'])
    def measurements(self, request, pk=None):
        return Response(ResultService.get_result_measurements(pk))

    @action(detail=True, methods=['post'], serializer_class=ClassifyRequestSerializer)
    @check_action_permission('manage_issues')
    def classify(self, request, pk=None):
        result = ResultService.get_result(pk)
        actor = get_user_by_access_token(request.COOKIES.get('access_token'))

        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        body = body_serializer.validated_data

        with transaction.atomic():
            issue = self._resolve_issue(body.get('issue'), actor)

            category = body['category']
            active = body['scope'] == 'future'
            origin = (
                RuleResultOrigin.MANUAL_PERSISTENT if active else RuleResultOrigin.MANUAL_ONEOFF
            )

            matcher = body.get('matcher') or {}
            rule_data = {
                'project': result.project_id,
                'issue': issue.id,
                'test': result.iteration.test_id,
                'category': category,
                'parameters': matcher.get(
                    'parameters',
                    ClassificationService.result_param_dict(result),
                ),
                'verdicts': matcher.get(
                    'verdicts',
                    sorted(ClassificationService.result_verdict_set(result)),
                ),
                'tags': matcher.get('tags', self._capture_tags(result)),
            }
            if 'expected' in body:
                rule_data['expected'] = body['expected']

            rule_serializer = IssueRuleSerializer(data=rule_data)
            rule_serializer.is_valid(raise_exception=True)
            rule = rule_serializer.save(created_by=actor, active=active)

            RuleResult.objects.get_or_create(
                result=result,
                issue_rule=rule,
                defaults={'origin': origin, 'created_by': actor},
            )

        RunCache.delete_data_for_obj(
            result.test_run,
            data_keys=RunCache.KEYS_CLASSIFICATION_AFFECTED,
        )

        data = ClassifyResponseSerializer({'issue_id': issue.id, 'rule_id': rule.id}).data
        return Response(data, status=status.HTTP_201_CREATED)

    def _resolve_issue(self, issue_data, actor):
        if isinstance(issue_data, int) or (
            isinstance(issue_data, str) and str(issue_data).isdigit()
        ):
            return get_object_or_404(Issue, pk=int(issue_data))
        serializer = IssueSerializer(data=issue_data)
        serializer.is_valid(raise_exception=True)
        return serializer.save(created_by=actor)

    def _capture_tags(self, result):
        important_by_run, relevant_by_run = get_tags_by_runs([result.test_run])
        return list(
            set(important_by_run.get(result.test_run_id, []))
            | set(relevant_by_run.get(result.test_run_id, [])),
        )
