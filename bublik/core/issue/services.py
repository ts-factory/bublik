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

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Count, Exists, F, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from bublik.core.cache import RunCache
from bublik.core.classification import (
    active_rule_subquery,
    active_rules_prefetch,
    any_rule_subquery,
)
from bublik.core.exceptions import NotFoundError
from bublik.core.issue.dto import BulkActionDTO
from bublik.core.run.classification import ClassificationService
from bublik.data.models import Issue, IssueRule, IssueState, RuleResult
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


def _validated(serializer):
    serializer.is_valid(raise_exception=True)
    return serializer


class IssueManagementService:
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
