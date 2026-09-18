# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import asdict

from django.db.models import OuterRef

from bublik.core.run.dto import RuleResultInfo
from bublik.data.models import IssueState, RuleResult


SUPPRESSION_FILTER = {'issue_rule__expected': True, 'issue_rule__issue__state': IssueState.OPEN}
SUPPRESSED_RELATION_FILTER = {
    f'rule_results__{key}': value for key, value in SUPPRESSION_FILTER.items()
}


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
