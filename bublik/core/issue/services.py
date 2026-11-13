# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Creating, changing, closing and deleting issues and their rules.

The REST viewsets and the MCP tools both go through here, so a change made
by an agent follows the same rules, and drops the same caches, as one made
in the browser. Permissions are the caller's business: they depend on how
the caller authenticated.
"""

from __future__ import annotations

from collections import Counter
import json

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Exists, F, IntegerField, Max, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django_filters.utils import translate_validation
from rest_framework.exceptions import ValidationError

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
from bublik.core.exceptions import NotFoundError
from bublik.core.issue.dto import (
    BulkActionDTO,
    IssueFacetsDTO,
    IssueRuleFacetsDTO,
    TestOptionDTO,
)
from bublik.core.issue.filters import IssueFilterSet, IssueRuleFilterSet
from bublik.core.run.classification import ClassificationService
from bublik.core.run.tests_organization import build_test_paths
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


def _per_issue_count(queryset, issue_field, count_field):
    """
    The number of distinct `count_field` values of `queryset` for the outer
    issue, as a scalar subquery: it is computed only for the issues a page
    returns (for every filtered issue when the list is ordered by it), not by
    joining every rule and stamp of every filtered issue before the page is
    cut.
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


def issues_with_rules_state():
    """Issues annotated with what the `rules` filter and rules_state read."""
    return Issue.objects.annotate(
        has_any_rule=Exists(any_rule_subquery()),
        has_active_rule=Exists(active_rule_subquery()),
    )


def issues_queryset():
    """The issues as a list shows them: with their counters and active rules."""
    return (
        issues_with_rules_state()
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


def rules_queryset():
    """The rules as a list shows them, with the fields it may be ordered by."""
    return (
        IssueRule.objects.select_related('issue__project', 'test')
        .annotate(test_name=F('test__name'), issue_title=F('issue__title'))
        .order_by('-created_at')
    )


def _filtered(filterset_class, queryset, query_params):
    """The queryset narrowed by the list filters; a malformed value is a 400."""
    filterset = filterset_class(data=query_params, queryset=queryset)
    if not filterset.is_valid():
        raise translate_validation(filterset.errors)
    return filterset.qs


def _ordered(queryset, ordering, ordering_fields):
    """
    The queryset in the order asked for: one of `ordering_fields`, with '-'
    for descending. The ID keeps the rows tying on it in a fixed order, so
    paging stays stable.
    """
    ordering = ordering or '-created_at'
    if ordering.removeprefix('-') not in ordering_fields:
        msg = f"Expected any of: {', '.join(ordering_fields)}, with '-' for descending."
        raise ValidationError({'ordering': msg})
    return queryset.order_by(ordering, '-id')


def _grouped_counts(qs, group_field, count_field='id'):
    """{value: count} for `qs` grouped by `group_field`, counting distinct `count_field`."""
    # Clear the default ordering: ORDER BY fields join GROUP BY and would
    # split every group into one row per timestamp.
    rows = qs.order_by().values(group_field).annotate(n=Count(count_field, distinct=True))
    return {row[group_field]: row['n'] for row in rows}


def _facets(query_params, filterset_class, base_qs, facet_params, count_fn):
    """For each dim, apply filterset_class with only that dim's own param removed."""
    # Validated in full first, as the list is: a malformed value is a 400,
    # not a filter silently left out.
    _filtered(filterset_class, base_qs, query_params)
    facets = {}
    for dim_key, param_name in facet_params.items():
        params = query_params.copy()
        params.pop(param_name, None)
        qs = filterset_class(data=params, queryset=base_qs).qs
        facets[dim_key] = count_fn(dim_key, qs)
    return facets


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


def _validated(serializer):
    serializer.is_valid(raise_exception=True)
    return serializer


class IssueManagementService:
    ORDERING_FIELDS = ('created_at', 'updated_at', 'title', 'state', 'result_count')

    @staticmethod
    def list(query_params, ordering: str | None = None):
        """
        The issues a list shows, narrowed by the list filters.

        Args:
            query_params: The filters IssueFilterSet reads (a QueryDict, or
                any MultiValueDict)
            ordering: One of ORDERING_FIELDS, with '-' for descending;
                newest first without it

        Returns:
            QuerySet of issues with their counters and active rules

        Raises:
            ValidationError: if a filter value or the ordering is not valid
        """
        return _ordered(
            _filtered(IssueFilterSet, issues_queryset(), query_params),
            ordering,
            IssueManagementService.ORDERING_FIELDS,
        )

    @staticmethod
    def facets(query_params) -> IssueFacetsDTO:
        """
        Count the issues by each value of the list filters (see IssueFacetsDTO).

        Args:
            query_params: The filters of the list, as list() takes them
        """
        # Over the bare issues: the counters of the list aren't counted here,
        # and joining them in would make every facet scan all the stamps.
        return IssueFacetsDTO(
            **_facets(
                query_params,
                IssueFilterSet,
                issues_with_rules_state(),
                _ISSUE_FACET_PARAMS,
                _issue_facet_counts,
            ),
        )

    @staticmethod
    def get(issue_id: int | str) -> Issue:
        """
        Get an issue by ID.

        Raises:
            NotFoundError: if the issue does not exist
        """
        try:
            return issues_queryset().get(pk=issue_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Issue {issue_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def projects_of(issue_ids) -> list[int]:
        """The projects of the issues that exist among the IDs, for a permission check."""
        return list(
            Issue.objects.filter(id__in=issue_ids).values_list('project_id', flat=True),
        )

    @staticmethod
    def validate(data: dict, instance: Issue | None = None) -> IssueSerializer:
        """
        Check the fields of a new issue, or of a change to ``instance``.

        Returns:
            The serializer, valid, for the caller to read ``validated_data``
            from before writing, as the permission may depend on it

        Raises:
            ValidationError: if the fields are invalid
        """
        return _validated(IssueSerializer(instance, data=data, partial=instance is not None))

    @staticmethod
    def create(serializer: IssueSerializer, actor) -> Issue:
        """Create the issue :meth:`validate` checked, recording ``actor``."""
        return serializer.save(created_by=actor)

    @staticmethod
    def update(serializer: IssueSerializer, actor) -> Issue:
        """
        Apply the change :meth:`validate` checked, recording ``actor``.

        Nothing a change may touch decides suppression: the state is changed
        by close/reopen, and the bug key and the project can't be changed once
        the issue has stamps, so no cache is dropped.
        """
        return serializer.save(updated_by=actor)

    @staticmethod
    def delete(issue: Issue) -> None:
        """Delete the issue with its rules and stamps, dropping the caches of their runs."""
        run_ids = ClassificationService.runs_for_issues([issue.id])
        issue.delete()
        RunCache.invalidate_classification_affected(run_ids)

    @staticmethod
    def close(issue_ids: list[int], actor) -> BulkActionDTO:
        """
        Close the open issues among the IDs, deactivating their rules: a
        closed issue classifies nothing new until it is reopened.
        """
        rows = list(Issue.objects.filter(id__in=issue_ids).values_list('id', 'state'))
        existing_ids = {issue_id for issue_id, _ in rows}
        updated_ids = [issue_id for issue_id, state in rows if state == IssueState.OPEN]

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
        return BulkActionDTO.split(issue_ids, existing_ids, updated_ids)

    @staticmethod
    def reopen(issue_ids: list[int], actor) -> BulkActionDTO:
        """Reopen the closed issues among the IDs; their rules stay deactivated."""
        rows = list(Issue.objects.filter(id__in=issue_ids).values_list('id', 'state'))
        existing_ids = {issue_id for issue_id, _ in rows}
        updated_ids = [issue_id for issue_id, state in rows if state == IssueState.CLOSED]
        Issue.objects.filter(id__in=updated_ids).update(
            state=IssueState.OPEN,
            closed_by=None,
            closed_at=None,
            updated_by=actor,
            updated_at=timezone.now(),
        )
        RunCache.invalidate_classification_affected(
            ClassificationService.runs_for_issues(updated_ids),
        )
        return BulkActionDTO.split(issue_ids, existing_ids, updated_ids)


class IssueRuleManagementService:
    ORDERING_FIELDS = ('created_at', 'category', 'active', 'test_name', 'issue_title')

    @staticmethod
    def list(query_params, ordering: str | None = None):
        """
        The rules a list shows, narrowed by the list filters.

        Args:
            query_params: The filters IssueRuleFilterSet reads (a QueryDict,
                or any MultiValueDict)
            ordering: One of ORDERING_FIELDS, with '-' for descending;
                newest first without it

        Returns:
            QuerySet of rules with their issues and tests

        Raises:
            ValidationError: if a filter value or the ordering is not valid
        """
        return _ordered(
            _filtered(IssueRuleFilterSet, rules_queryset(), query_params),
            ordering,
            IssueRuleManagementService.ORDERING_FIELDS,
        )

    @staticmethod
    def facets(query_params) -> IssueRuleFacetsDTO:
        """
        Count the rules by each value of the list filters (see IssueRuleFacetsDTO).

        Args:
            query_params: The filters of the list, as list() takes them
        """
        return IssueRuleFacetsDTO(
            **_facets(
                query_params,
                IssueRuleFilterSet,
                rules_queryset(),
                _RULE_FACET_PARAMS,
                _rule_facet_counts,
            ),
        )

    @staticmethod
    def test_options(project_id=None, search: str = '') -> list[TestOptionDTO]:
        """
        The tests a rule may belong to: up to 20 tests whose full path
        contains `search`, or the 10 most recently ruled tests without it.
        Only tests that have results (in the given project, if any).

        A test's name alone does not identify it - the same name can occur
        under different parent packages with a different id each time - so
        `search` matches the full path.
        """
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
        return [
            TestOptionDTO(
                id=test_id,
                name=tests_by_id[test_id].name,
                path=paths_by_id.get(test_id, ''),
            )
            for test_id in ids
            if test_id in tests_by_id
        ]

    @staticmethod
    def get(rule_id: int | str) -> IssueRule:
        """
        Get a rule by ID.

        Raises:
            NotFoundError: if the rule does not exist
        """
        try:
            return rules_queryset().get(pk=rule_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Rule {rule_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def projects_of(rule_ids) -> list[int]:
        """The projects of the rules that exist among the IDs, for a permission check."""
        return list(
            IssueRule.objects.filter(id__in=rule_ids).values_list(
                'issue__project_id',
                flat=True,
            ),
        )

    @staticmethod
    def validate(
        data: dict,
        instance: IssueRule | None = None,
        matcher_checked: bool = False,
    ) -> IssueRuleSerializer:
        """
        Check the fields of a new rule, or of a change to ``instance``.

        ``matcher_checked`` tells that the matcher is known to fit the results
        it is made for, so it is not looked for in the database again.

        Returns:
            The serializer, valid, for the caller to read ``validated_data``
            from before writing, as the permission may depend on it

        Raises:
            ValidationError: if the fields are invalid
        """
        return _validated(
            IssueRuleSerializer(
                instance,
                data=data,
                partial=instance is not None,
                context={'matcher_checked': matcher_checked},
            ),
        )

    @staticmethod
    def create(serializer: IssueRuleSerializer, actor, **fields) -> IssueRule:
        """Create the rule :meth:`validate` checked, recording ``actor``."""
        return serializer.save(created_by=actor, **fields)

    @staticmethod
    def update(serializer: IssueRuleSerializer, actor) -> IssueRule:
        """
        Apply the change :meth:`validate` checked, recording ``actor``.

        Of what may change on a rule with stamps, only whether its results
        are expected decides what they suppress, so only that change drops
        the caches of the rule's runs.
        """
        rule = serializer.instance
        expected_changed = (
            'expected' in serializer.validated_data
            and serializer.validated_data['expected'] != rule.expected
        )
        rule = serializer.save(updated_by=actor)
        if expected_changed:
            RunCache.invalidate_classification_affected(
                ClassificationService.runs_for_rules([rule.id]),
            )
        return rule

    @staticmethod
    def delete(rule: IssueRule) -> None:
        """Delete the rule with its stamps, dropping the caches of their runs."""
        run_ids = ClassificationService.runs_for_rules([rule.id])
        rule.delete()
        RunCache.invalidate_classification_affected(run_ids)

    @staticmethod
    def activate(rule_ids: list[int], actor) -> BulkActionDTO:
        """
        Activate the inactive rules among the IDs. Whether a rule is active
        doesn't decide what its stamps suppress, so no cache is dropped.

        Raises:
            ValidationError: if a rule belongs to a closed issue, which
                deactivated it: the issue has to be reopened first
        """
        rows = list(
            IssueRule.objects.filter(id__in=rule_ids).values_list(
                'id',
                'active',
                'issue__state',
            ),
        )
        existing_ids = {rule_id for rule_id, _, _ in rows}
        updated_ids = [rule_id for rule_id, active, _ in rows if not active]

        closed_issue_rule_ids = [
            rule_id
            for rule_id, active, issue_state in rows
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
            updated_by=actor,
            updated_at=timezone.now(),
        )
        return BulkActionDTO.split(rule_ids, existing_ids, updated_ids)

    @staticmethod
    def deactivate(rule_ids: list[int], actor) -> BulkActionDTO:
        """Deactivate the active rules among the IDs; their stamps stay."""
        rows = list(IssueRule.objects.filter(id__in=rule_ids).values_list('id', 'active'))
        existing_ids = {rule_id for rule_id, _ in rows}
        updated_ids = [rule_id for rule_id, active in rows if active]

        now = timezone.now()
        IssueRule.objects.filter(id__in=updated_ids).update(
            active=False,
            deactivated_by=actor,
            deactivated_at=now,
            updated_by=actor,
            updated_at=now,
        )
        return BulkActionDTO.split(rule_ids, existing_ids, updated_ids)
