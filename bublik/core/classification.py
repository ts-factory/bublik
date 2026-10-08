# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import asdict

from django.db.models import BooleanField, ExpressionWrapper, OuterRef, Prefetch, Q, TextChoices

from bublik.core.run.dto import RuleResultInfo
from bublik.data.models import IssueRule, IssueState, RuleResult


SUPPRESSION_FILTER = {'issue_rule__expected': True, 'issue_rule__issue__state': IssueState.OPEN}
SUPPRESSED_RELATION_FILTER = {
    f'rule_results__{key}': value for key, value in SUPPRESSION_FILTER.items()
}
RULE_SUPPRESSION_FILTER = {
    key.removeprefix('issue_rule__'): value for key, value in SUPPRESSION_FILTER.items()
}

DISPOSITION_TO_EXPECTED = {'expected': True, 'unexpected': False, 'none': None}


class Effect(TextChoices):
    SUPPRESSED = 'suppressed'
    STALE = 'stale'
    UNEXPECTED = 'unexpected'
    MARKED = 'marked'


def effect_for(is_suppressed, expected, issue_state):
    """
    Classify a rule's effect on a run's results, from its own disposition
    and its issue's state:
      - suppressed - expected, and its issue is still open: the rule
        suppresses the unexpectedness of any result it stamps
      - stale      - its issue is closed; reopening an issue does not
        reactivate its rules, so a closed issue's classification is frozen
        from when it was open
      - unexpected - explicitly not expected (issue open)
      - marked     - none of the above: a marker-only rule (expected is
        None) on an open issue
    """
    if is_suppressed:
        return Effect.SUPPRESSED
    if issue_state == IssueState.CLOSED:
        return Effect.STALE
    if expected is False:
        return Effect.UNEXPECTED
    return Effect.MARKED


class RulesState(TextChoices):
    ENFORCED = 'enforced'
    DORMANT = 'dormant'
    DEACTIVATED = 'deactivated'
    UNRULED = 'unruled'


def rules_state_for(has_any_rule, has_active_rule, issue_state):
    """
    Derive an issue's rules_state from has_any_rule/has_active_rule - plus
    the issue's own state:
      - no rules at all                        -> unruled
      - at least one active rule                -> enforced
      - rules exist, none active, issue closed  -> deactivated
      - rules exist, none active, issue open    -> dormant

    dormant is kept distinct from deactivated because reopening an issue
    does not reactivate its rules.
    """
    if not has_any_rule:
        return RulesState.UNRULED
    if has_active_rule:
        return RulesState.ENFORCED
    if issue_state == IssueState.CLOSED:
        return RulesState.DEACTIVATED
    return RulesState.DORMANT


def any_rule_subquery(outer_field='id'):
    """
    IssueRule rows attached to the outer issue, active or not.
    Use inside Exists(): Exists(any_rule_subquery()).
    """
    return IssueRule.objects.filter(issue_id=OuterRef(outer_field))


def active_rule_subquery(outer_field='id'):
    """
    IssueRule rows that are currently active for the outer issue.
    Use inside Exists(): Exists(active_rule_subquery()).
    """
    return IssueRule.objects.filter(issue_id=OuterRef(outer_field), active=True)


def rules_state_query(states):
    """
    Q object matching issues whose rules_state (see rules_state_for()) is
    one of `states`. Requires the issue queryset to be annotated with
    has_any_rule and has_active_rule (see any_rule_subquery()/
    active_rule_subquery()).
    """
    query = Q()
    if RulesState.UNRULED in states:
        query |= Q(has_any_rule=False)
    if RulesState.ENFORCED in states:
        query |= Q(has_active_rule=True)
    if RulesState.DEACTIVATED in states:
        query |= Q(has_any_rule=True, has_active_rule=False, state=IssueState.CLOSED)
    if RulesState.DORMANT in states:
        query |= Q(has_any_rule=True, has_active_rule=False, state=IssueState.OPEN)
    return query


def disposition_query(dispositions):
    """
    Q object matching IssueRule rows whose `expected` is any of
    `dispositions` - the API's vocabulary for the field's three states
    ('expected'/'unexpected'/'none' for True/False/NULL).
    """
    query = Q()
    for disposition in dispositions:
        expected = DISPOSITION_TO_EXPECTED[disposition]
        query |= Q(expected__isnull=True) if expected is None else Q(expected=expected)
    return query


def active_rules_prefetch():
    """
    Prefetch an issue's active rules in one batched query, annotated with
    is_suppressed (via RULE_SUPPRESSION_FILTER) so effect_for() can be
    computed per rule without a query per rule. Attach with
    .prefetch_related(active_rules_prefetch()); reads back via the
    '_active_rules_cache' attribute it fills in.
    """
    return Prefetch(
        'rules',
        queryset=IssueRule.objects.filter(active=True)
        .annotate(
            is_suppressed=ExpressionWrapper(
                Q(**RULE_SUPPRESSION_FILTER),
                output_field=BooleanField(),
            ),
        )
        .order_by('id'),
        to_attr='_active_rules_cache',
    )


def suppressed_subquery(outer_field='id'):
    """
    RuleResult rows suppressing the outer result's unexpectedness.
    Use inside Exists(): Exists(suppressed_subquery()).
    """
    return RuleResult.objects.filter(result_id=OuterRef(outer_field), **SUPPRESSION_FILTER)


def build_rule_result_info(rule_result) -> RuleResultInfo:
    """
    Build a display-oriented view of a RuleResult stamp.

    Args:
        rule_result: A RuleResult instance

    Returns:
        RuleResultInfo with the classification details for display
    """
    rule = rule_result.issue_rule
    return RuleResultInfo(
        issue_id=rule.issue_id,
        issue_title=rule.issue.title,
        issue_state=rule.issue.state,
        bug_key=rule.issue.bug_key,
        bug_url=rule.issue.bug_url,
        category=rule.category,
        expected=rule.expected,
        rule_id=rule.id,
        origin=rule_result.origin,
    )


def build_issues_list(result) -> list[dict]:
    """
    Build the 'issues' list for a TestIterationResult, for API display.

    Args:
        result: A TestIterationResult instance

    Returns:
        List of classification dicts, one per RuleResult stamped on the result
    """
    rule_results = result.rule_results.select_related('issue_rule__issue').all()
    return [asdict(build_rule_result_info(rr)) for rr in rule_results]
