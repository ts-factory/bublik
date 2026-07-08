# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.db.models import OuterRef, TextChoices

from bublik.data.models import IssueState, RuleResult


SUPPRESSION_FILTER = {'issue_rule__expected': True, 'issue_rule__issue__state': IssueState.OPEN}
RULE_SUPPRESSION_FILTER = {
    key.removeprefix('issue_rule__'): value for key, value in SUPPRESSION_FILTER.items()
}


def suppressed_subquery(outer_field='id'):
    """
    RuleResult rows suppressing the outer result's unexpectedness.
    Use inside Exists(): Exists(suppressed_subquery()).
    """
    return RuleResult.objects.filter(result_id=OuterRef(outer_field), **SUPPRESSION_FILTER)


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
      - stale      - its issue is closed: the stamp stays on the result as a
        record but no longer suppresses anything. Reopening an issue does
        not reactivate its rules, and re-importing the run keeps the stamps
        of inactive rules
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
