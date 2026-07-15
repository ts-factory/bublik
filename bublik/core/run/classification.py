# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from collections import defaultdict

from django.conf import settings
from django.db import transaction
from django.db.models import BooleanField, Count, ExpressionWrapper, Q
from rest_framework.exceptions import ValidationError

from bublik.core.classification import SUPPRESSION_FILTER, Effect, effect_for
from bublik.core.logging import get_task_or_server_logger
from bublik.core.references import resolve_ref
from bublik.core.run.data import get_parameters_by_iterations, get_verdicts
from bublik.core.utils import key_value_transforming
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
    def active_rules_for(run: models.TestIterationResult) -> list[models.IssueRule]:
        """
        Get the rules that apply to a run: the active rules of the open issues
        of its project (closing an issue deactivates its rules).

        Args:
            run: The run the rules are to be applied to

        Returns:
            List of IssueRule instances
        """
        return list(
            models.IssueRule.objects.filter(
                issue__project_id=run.project_id,
                issue__state=models.IssueState.OPEN,
                active=True,
            ),
        )

    @staticmethod
    def run_tags(run: models.TestIterationResult) -> set[str]:
        """
        Get all the tags of a run, categorized or not, as the rules capture
        them ('name=value', or 'name' for a tag without a value).

        Args:
            run: The run whose tags to get

        Returns:
            Set of tag strings
        """
        return {
            key_value_transforming(name, value)
            for name, value in models.MetaResult.objects.filter(
                result=run,
                meta__type='tag',
            ).values_list('meta__name', 'meta__value')
        }

    @staticmethod
    def match_rules(
        rules: list[models.IssueRule],
        run: models.TestIterationResult,
    ) -> dict[int, list[int]]:
        """
        Find the results of a run that each of the rules matches.

        A result matches a rule when it is unexpected (has an `err` meta), its
        test is the rule's test, the rule's tags are all among the tags of the
        run, and the rule's parameters and verdicts are a subset of the
        result's own (an empty one is not applied). The unexpected results of
        the run, their parameters and verdicts, and the tags of the run are
        loaded once for all the rules, so the number of queries doesn't grow
        with the number of rules or results.

        A rule that fails to match is logged and left out of the result, so
        that its stamps stay as they are and the other rules still apply.

        Args:
            rules: The issue rules to match
            run: The run whose results are searched

        Returns:
            Dict mapping the ID of each rule matched without failing to the
            IDs of the results it matches (possibly none)
        """
        logger = get_task_or_server_logger()

        # Only unexpected results (those with an `err` meta) are candidates, as
        # in the `untriaged` filter and the suppression: a rule with a loose
        # matcher must not stamp the results that went as expected.
        candidates_by_test = defaultdict(list)
        for result_id, iteration_id, test_id in (
            models.TestIterationResult.objects.filter(
                test_run=run,
                iteration__test_id__in={rule.test_id for rule in rules},
                meta_results__meta__type='err',
            )
            .values_list('id', 'iteration_id', 'iteration__test_id')
            .distinct()
        ):
            candidates_by_test[test_id].append((result_id, iteration_id))
        candidates = [
            candidate
            for test_candidates in candidates_by_test.values()
            for candidate in test_candidates
        ]

        parameters_by_iterations = (
            get_parameters_by_iterations([iteration_id for _, iteration_id in candidates])
            if candidates and any(rule.parameters for rule in rules)
            else {}
        )
        verdicts_by_result = (
            get_verdicts([result_id for result_id, _ in candidates])
            if candidates and any(rule.verdicts for rule in rules)
            else {}
        )
        run_tags = (
            ClassificationService.run_tags(run) if any(rule.tags for rule in rules) else set()
        )

        matches = {}
        for rule in rules:
            try:
                if not set(rule.tags).issubset(run_tags):
                    matches[rule.id] = []
                    continue
                matches[rule.id] = [
                    result_id
                    for result_id, iteration_id in candidates_by_test.get(rule.test_id, ())
                    if rule.parameters.items()
                    <= parameters_by_iterations.get(iteration_id, {}).items()
                    and set(rule.verdicts).issubset(verdicts_by_result.get(result_id, ()))
                ]
            except Exception:
                logger.exception(
                    f'skipping classification rule {rule.id}: failed on run {run.id}'
                )
        return matches

    @staticmethod
    def create_stamps(
        matches: dict[int, list[int]],
        run: models.TestIterationResult,
        origin: str,
        actor: models.User | None = None,
    ) -> int:
        """
        Stamp the results of a run with the rules that match them, all at
        once, leaving the stamps that already exist as they are.

        Args:
            matches: The output of match_rules()
            run: The run the results belong to
            origin: The origin of the new stamps
            actor: The user who triggered the action, stored as created_by
                on the new stamps

        Returns:
            Number of RuleResult stamps created
        """
        existing = set(
            models.RuleResult.objects.filter(
                result__test_run=run,
                issue_rule_id__in=list(matches),
            ).values_list('result_id', 'issue_rule_id'),
        )
        stamps = [
            models.RuleResult(
                result_id=result_id,
                issue_rule_id=rule_id,
                origin=origin,
                created_by=actor,
            )
            for rule_id, result_ids in matches.items()
            for result_id in result_ids
            if (result_id, rule_id) not in existing
        ]
        models.RuleResult.objects.bulk_create(stamps, ignore_conflicts=True, batch_size=1000)
        return len(stamps)

    @staticmethod
    def apply_active_rules(run: models.TestIterationResult) -> int:
        """
        Recompute import-origin RuleResult stamps for a run from the project's
        active rules. Idempotent: for each rule that is active now, drops its
        previous origin='import' stamps and rebuilds them, so a stamp that no
        longer fits its rule goes away. The stamps of deactivated rules and the
        manual stamps (manual_persistent / manual_oneoff) are never touched.
        Only the rules of open issues apply: closing an issue deactivates its
        rules. A rule that fails to apply is logged and skipped (its stamps
        stay as they were), so one broken rule can't block the others.

        Args:
            run: The run to (re)apply active rules to, typically right after import

        Returns:
            Number of RuleResult stamps created
        """
        with transaction.atomic():
            matches = ClassificationService.match_rules(
                ClassificationService.active_rules_for(run),
                run,
            )
            # Only the rules matched without failing: a failing rule keeps the
            # stamps it has.
            models.RuleResult.objects.filter(
                result__test_run=run,
                origin=models.RuleResultOrigin.IMPORT,
                issue_rule_id__in=list(matches),
            ).delete()
            return ClassificationService.create_stamps(
                matches,
                run,
                models.RuleResultOrigin.IMPORT,
            )

    @staticmethod
    def apply_rules_after_import(run: models.TestIterationResult) -> None:
        """
        Classify a run's results as the last step of its import, regular or
        live, by applying the project's active rules (see apply_active_rules).
        Best-effort: a failure is logged with its traceback and doesn't fail
        the import.

        Args:
            run: The run just imported
        """
        logger = get_task_or_server_logger()
        try:
            stamps_created = ClassificationService.apply_active_rules(run)
            logger.info(f'the number of classification stamps applied is {stamps_created}')
        except Exception:
            logger.exception('failed to apply classification rules during import, continuing')

    @staticmethod
    def apply_active_rules_manual(
        run: models.TestIterationResult,
        actor: models.User | None = None,
    ) -> int:
        """
        Apply the project's active rules to an already-imported run on demand.

        Existing RuleResult stamps are never removed - this only adds stamps
        that are missing, so it is safe to call repeatedly on the same run.
        Only the rules of open issues apply: closing an issue deactivates its
        rules. A rule that fails to match is logged and skipped, so one broken
        rule can't block the others.

        Args:
            run: The run to apply active rules to
            actor: The user who triggered the action, stored as created_by
                on any new stamps

        Returns:
            Number of RuleResult stamps actually created
        """
        matches = ClassificationService.match_rules(
            ClassificationService.active_rules_for(run),
            run,
        )
        with transaction.atomic():
            return ClassificationService.create_stamps(
                matches,
                run,
                models.RuleResultOrigin.MANUAL_PERSISTENT,
                actor=actor,
            )

    @staticmethod
    def runs_for_rules(rule_ids: list[int]) -> list[int]:
        """
        Get run IDs that have at least one stamp for any of the given rules.

        Args:
            rule_ids: The issue rule IDs to look up

        Returns:
            List of distinct TestIterationResult (run) IDs
        """
        return list(
            models.RuleResult.objects.filter(issue_rule_id__in=rule_ids)
            .values_list('result__test_run_id', flat=True)
            .distinct(),
        )

    @staticmethod
    def runs_for_issues(issue_ids: list[int]) -> list[int]:
        """
        Get run IDs that have at least one stamp for any rule of any of the given issues.

        Args:
            issue_ids: The issue IDs to look up

        Returns:
            List of distinct TestIterationResult (run) IDs
        """
        return list(
            models.RuleResult.objects.filter(issue_rule__issue_id__in=issue_ids)
            .values_list('result__test_run_id', flat=True)
            .distinct(),
        )

    @staticmethod
    def run_issues_summary(run: models.TestIterationResult) -> list[dict]:
        """
        Per-issue summary of classified results in a run.

        Args:
            run: The run to summarize

        Returns:
            List of dicts, one per issue with at least one stamp in this run:
            issue_id, title, description, state, bug_key, bug_url,
            result_count (distinct results stamped under this issue in this
            run), and `rules` - one entry per issue_rule that stamped a
            result here (rule_id, category, expected, effect).
        """
        run_rule_results = models.RuleResult.objects.filter(result__test_run=run).annotate(
            is_suppressed=ExpressionWrapper(
                Q(**SUPPRESSION_FILTER),
                output_field=BooleanField(),
            ),
        )

        # Distinct result count per issue (a result may carry stamps from
        # several of the issue's rules).
        result_counts = {
            rrr['issue_rule__issue_id']: rrr['result_count']
            for rrr in run_rule_results.values('issue_rule__issue_id').annotate(
                result_count=Count('result_id', distinct=True),
            )
        }

        issues_by_id: dict = {}
        for rrr in (
            run_rule_results.values(
                'issue_rule_id',
                'issue_rule__issue_id',
                'issue_rule__issue__title',
                'issue_rule__issue__description',
                'issue_rule__issue__state',
                'issue_rule__issue__bug_key',
                'issue_rule__category',
                'issue_rule__expected',
                'is_suppressed',
            )
            .order_by('issue_rule_id')
            .distinct()
        ):
            issue_id = rrr['issue_rule__issue_id']
            issue_summary = issues_by_id.setdefault(
                issue_id,
                {
                    'issue_id': issue_id,
                    'title': rrr['issue_rule__issue__title'],
                    'description': rrr['issue_rule__issue__description'],
                    'state': rrr['issue_rule__issue__state'],
                    'bug_key': rrr['issue_rule__issue__bug_key'],
                    'rules': [],
                    'result_count': result_counts.get(issue_id, 0),
                },
            )
            issue_summary['rules'].append(
                {
                    'rule_id': rrr['issue_rule_id'],
                    'category': rrr['issue_rule__category'],
                    'expected': rrr['issue_rule__expected'],
                    'effect': effect_for(
                        rrr['is_suppressed'],
                        rrr['issue_rule__expected'],
                        rrr['issue_rule__issue__state'],
                    ),
                },
            )

        for issue_summary in issues_by_id.values():
            bug_key = issue_summary['bug_key']
            resolved = resolve_ref(bug_key, run.project_id) if bug_key else None
            issue_summary['bug_url'] = resolved[2] if resolved else None

        return sorted(issues_by_id.values(), key=lambda x: (x['title'] or '').lower())

    @staticmethod
    def filter_issues_summary(
        issue_summaries: list[dict],
        search: str | None = None,
        state: str | None = None,
        category: str | None = None,
        effect: str | None = None,
    ) -> list[dict]:
        """
        Filter the output of run_issues_summary() by title/bug_key search,
        issue state, rule category, and rule effect.

        Applied to the already-built summary, not pushed into the queryset
        that builds it - result_count and rules there are counted against
        every stamp in the run, and filtering earlier would change what
        those numbers mean.

        Args:
            issue_summaries: Output of run_issues_summary()
            search: Optional title/bug_key substring match
            state: Optional `;`-list of issue states to keep
            category: Optional `;`-list of rule categories - an issue summary
                is kept if any of its rules has one of them
            effect: Optional `;`-list of rule effects - an issue summary is
                kept if any of its rules has one of them

        Returns:
            The filtered issue summaries

        Raises:
            ValidationError: if state/category/effect carries an unknown value
        """
        if search:
            needle = search.strip().lower()
            if needle:
                issue_summaries = [
                    issue_summary
                    for issue_summary in issue_summaries
                    if needle in (issue_summary['title'] or '').lower()
                    or needle in (issue_summary['bug_key'] or '').lower()
                ]

        if state:
            states = state.split(settings.QUERY_DELIMITER)
            if any(s not in models.IssueState.values for s in states):
                msg = f'Expected any of: {", ".join(models.IssueState.values)}.'
                raise ValidationError({'state': msg})
            issue_summaries = [
                issue_summary
                for issue_summary in issue_summaries
                if issue_summary['state'] in states
            ]

        if category:
            categories = category.split(settings.QUERY_DELIMITER)
            if any(c not in models.IssueCategory.values for c in categories):
                msg = f'Expected any of: {", ".join(models.IssueCategory.values)}.'
                raise ValidationError({'category': msg})
            issue_summaries = [
                issue_summary
                for issue_summary in issue_summaries
                if any(rule['category'] in categories for rule in issue_summary['rules'])
            ]

        if effect:
            effects = effect.split(settings.QUERY_DELIMITER)
            if any(e not in Effect.values for e in effects):
                msg = f'Expected any of: {", ".join(Effect.values)}.'
                raise ValidationError({'effect': msg})
            issue_summaries = [
                issue_summary
                for issue_summary in issue_summaries
                if any(rule['effect'] in effects for rule in issue_summary['rules'])
            ]

        return issue_summaries
