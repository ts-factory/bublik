# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import check_action_permission_for_projects, get_user_by_access_token
from bublik.core.cache import RunCache
from bublik.core.result import ResultService
from bublik.core.run.stats import (
    generate_results_details,
)
from bublik.data.models import (
    Issue,
    IssueRule,
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
    ClassifyOptionsRequestSerializer,
    ClassifyOptionsResponseSerializer,
    ClassifyRequestSerializer,
    ClassifyResponseSerializer,
    UnclassifyRequestSerializer,
    UnclassifyResponseSerializer,
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
        issue = self.request.query_params.get('issue')

        return ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
            start_exec_seqno=start_exec_seqno,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
            issue=issue,
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

    @action(
        detail=False,
        methods=['post'],
        url_path='classify/options',
        serializer_class=ClassifyOptionsRequestSerializer,
    )
    def classify_options(self, request):
        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        result_ids = body_serializer.validated_data['result_ids']

        found = ResultService.get_results_by_ids(result_ids)
        options = ResultService.get_classify_options(
            ResultService.validate_results_for_classification(result_ids, found),
        )
        return Response(ClassifyOptionsResponseSerializer(options).data)

    @action(detail=False, methods=['post'], serializer_class=ClassifyRequestSerializer)
    def classify(self, request):
        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        body = body_serializer.validated_data
        result_ids = body['result_ids']

        found = ResultService.get_results_by_ids(result_ids)
        # Before anything is told about the results, as it depends on their projects.
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [result.project_id for result in found.values()],
        )
        results = ResultService.validate_results_for_classification(result_ids, found)
        options = ResultService.get_classify_options(results)
        ResultService.validate_classify_matchers(options, body['matchers'])
        matchers = {matcher['test_id']: matcher for matcher in body['matchers']}
        actor = get_user_by_access_token(request.COOKIES.get('access_token'))

        with transaction.atomic():
            issue = self._resolve_issue(body.get('issue'), results[0], actor)

            active = body['scope'] == 'future'
            origin = (
                RuleResultOrigin.MANUAL_PERSISTENT if active else RuleResultOrigin.MANUAL_ONEOFF
            )
            # The given disposition, if any, else the category's own.
            disposition = {'expected': body['expected']} if 'expected' in body else {}

            # A rule belongs to one test, so each test among the results gets
            # a rule per category, matching all of that test's results.
            rule_ids = []
            for test in options.tests:
                matcher = matchers[test.test_id]
                matcher_data = {
                    'parameters': matcher['parameters'],
                    'verdicts': sorted(matcher['verdicts']),
                    'tags': sorted(matcher['tags']),
                }
                for category in body['categories']:
                    rule = self._get_or_create_rule(
                        test.test_id,
                        issue,
                        category,
                        matcher_data,
                        disposition=disposition,
                        active=active,
                        actor=actor,
                    )
                    # A result already stamped with the rule keeps its stamp.
                    RuleResult.objects.bulk_create(
                        [
                            RuleResult(
                                result=result,
                                issue_rule=rule,
                                origin=origin,
                                created_by=actor,
                            )
                            for result in test.results
                        ],
                        ignore_conflicts=True,
                    )
                    rule_ids.append(rule.id)

        RunCache.delete_data_for_obj(
            results[0].test_run,
            data_keys=RunCache.KEYS_CLASSIFICATION_AFFECTED,
        )

        data = ClassifyResponseSerializer({'issue_id': issue.id, 'rule_ids': rule_ids}).data
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], serializer_class=UnclassifyRequestSerializer)
    def unclassify(self, request):
        body_serializer = self.get_serializer(data=request.data)
        body_serializer.is_valid(raise_exception=True)
        body = body_serializer.validated_data

        found = ResultService.get_rule_results_by_ids(body['rule_result_ids'])
        # Before anything is told about the stamps, as it depends on their projects.
        check_action_permission_for_projects(
            request,
            'manage_issues',
            [rule_result.result.project_id for rule_result in found.values()],
        )

        with transaction.atomic():
            outcome = ResultService.unclassify_results(
                body['rule_result_ids'],
                found,
            )

        runs = {
            rule_result.result.test_run_id: rule_result.result.test_run
            for rule_result in found.values()
        }
        for run in runs.values():
            RunCache.delete_data_for_obj(run, data_keys=RunCache.KEYS_CLASSIFICATION_AFFECTED)

        return Response(UnclassifyResponseSerializer(outcome).data)

    def _get_or_create_rule(
        self,
        test_id,
        issue,
        category,
        matcher_data,
        *,
        disposition,
        active,
        actor,
    ):
        rule_data = {
            'issue': issue.id,
            'test': test_id,
            'category': category,
            **matcher_data,
            **disposition,
        }

        rule_serializer = IssueRuleSerializer(data=rule_data)
        rule_serializer.is_valid(raise_exception=True)

        # Repeating a classification reuses the identical rule (same matcher,
        # category, disposition and active state) instead of piling up copies;
        # the result just gets one more stamp on it.
        fields = rule_serializer.validated_data
        rule = (
            IssueRule.objects.filter(
                issue=fields['issue'],
                test=fields['test'],
                category=fields['category'],
                expected=fields['expected'],
                parameters=fields['parameters'],
                verdicts=fields['verdicts'],
                tags=fields['tags'],
                active=active,
            )
            .order_by('id')
            .first()
        )
        if rule is not None:
            return rule

        # A one-off rule only carries the stamp and never applies to imports,
        # so it is created already deactivated.
        deactivation = (
            {} if active else {'deactivated_at': timezone.now(), 'deactivated_by': actor}
        )
        return rule_serializer.save(created_by=actor, active=active, **deactivation)

    def _resolve_issue(self, issue_data, result, actor):
        if isinstance(issue_data, int) or (
            isinstance(issue_data, str) and str(issue_data).isdigit()
        ):
            issue = Issue.objects.filter(pk=int(issue_data)).first()
            if issue is None:
                raise ValidationError({'issue': f'Issue #{int(issue_data)} not found.'})
            if issue.project_id != result.project_id:
                raise ValidationError(
                    {'issue': f'Issue #{issue.id} belongs to a different project.'},
                )
            return issue
        issue_data = {**issue_data, 'project': result.project_id}
        serializer = IssueSerializer(data=issue_data)
        if not serializer.is_valid():
            raise ValidationError({'issue': serializer.errors})
        return serializer.save(created_by=actor)
