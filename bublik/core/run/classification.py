# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from bublik.core.run.data import get_tags_by_runs
from bublik.data import models


class ClassificationService:
    """
    Matcher engine: applies active IssueRules to a run's results, creating
    RuleResult stamps. The test path is always required for a match;
    parameters and verdicts are matched as a subset when non-empty (an empty
    value means that criterion is not applied). Tag matching is a run-level
    gate, since all results in a run share the run's tags.
    """

    @staticmethod
    def result_param_dict(result: models.TestIterationResult) -> dict:
        """
        Build a {name: value} dict of a result's test arguments.

        Args:
            result: The test iteration result

        Returns:
            Dictionary mapping argument names to their values
        """
        return {a.name: a.value for a in result.iteration.test_arguments.all()}

    @staticmethod
    def result_verdict_set(result: models.TestIterationResult) -> set[str]:
        """
        Collect a result's verdict strings.

        Args:
            result: The test iteration result

        Returns:
            Set of verdict values attached to the result
        """
        return set(
            result.meta_results.filter(meta__type='verdict').values_list(
                'meta__value',
                flat=True,
            ),
        )

    @staticmethod
    def rule_matches_run_tags(rule: models.IssueRule, run: models.TestIterationResult) -> bool:
        """
        Check the run-level tag gate for a rule.

        Args:
            rule: The issue rule to test
            run: The run whose tags are checked against the rule

        Returns:
            True if rule.tags is empty (no gate) or is a subset of the run's
            tags (important + relevant); False otherwise
        """
        if not rule.tags:
            return True

        important_by_run, relevant_by_run = get_tags_by_runs([run])
        universe = set(important_by_run.get(run.id, [])) | set(
            relevant_by_run.get(run.id, []),
        )
        return set(rule.tags).issubset(universe)

    @staticmethod
    def matching_results(
        rule: models.IssueRule,
        run: models.TestIterationResult,
    ) -> list[models.TestIterationResult]:
        """
        Find results in a run that match a rule.

        A result matches when its test equals rule.test and, for each
        non-empty matcher field on the rule (parameters, verdicts), the
        rule's captured value is a subset of the result's own value. The
        run-level tag gate (rule.tags) is checked once via
        rule_matches_run_tags before iterating over candidate results.

        Args:
            rule: The issue rule to match
            run: The run whose results are searched

        Returns:
            List of matching TestIterationResult instances
        """
        if not ClassificationService.rule_matches_run_tags(rule, run):
            return []

        candidates = models.TestIterationResult.objects.filter(
            test_run=run,
            iteration__test_id=rule.test_id,
        ).select_related('iteration')

        matched = []
        for result in candidates:
            if rule.parameters and not (
                rule.parameters.items()
                <= ClassificationService.result_param_dict(result).items()
            ):
                continue
            if rule.verdicts and not set(rule.verdicts).issubset(
                ClassificationService.result_verdict_set(result),
            ):
                continue
            matched.append(result)
        return matched

    @staticmethod
    def apply_active_rules_manual(
        run: models.TestIterationResult,
        actor: models.User | None = None,
    ) -> int:
        """
        Apply the project's active rules to an already-imported run on demand.

        Existing RuleResult stamps are never removed - this only adds stamps
        that are missing, so it is safe to call repeatedly on the same run.

        Args:
            run: The run to apply active rules to
            actor: The user who triggered the action, stored as created_by
                on any new stamps

        Returns:
            Number of RuleResult stamps actually created
        """
        rules = models.IssueRule.objects.filter(project_id=run.project_id, active=True)
        created = 0
        for rule in rules:
            for result in ClassificationService.matching_results(rule, run):
                _, made = models.RuleResult.objects.get_or_create(
                    result=result,
                    issue_rule=rule,
                    defaults={
                        'origin': models.RuleResultOrigin.MANUAL_PERSISTENT,
                        'created_by': actor,
                    },
                )
                created += int(made)
        return created

    @staticmethod
    def runs_for_rule(rule: models.IssueRule) -> list[int]:
        """
        Get run IDs that have at least one stamp for a rule.

        Args:
            rule: The issue rule to look up

        Returns:
            List of distinct TestIterationResult (run) IDs
        """
        return list(
            models.RuleResult.objects.filter(issue_rule=rule)
            .values_list('result__test_run_id', flat=True)
            .distinct(),
        )

    @staticmethod
    def runs_for_issue(issue: models.Issue) -> list[int]:
        """
        Get run IDs that have at least one stamp for any rule of an issue.

        Args:
            issue: The issue to look up

        Returns:
            List of distinct TestIterationResult (run) IDs
        """
        return list(
            models.RuleResult.objects.filter(issue_rule__issue=issue)
            .values_list('result__test_run_id', flat=True)
            .distinct(),
        )
