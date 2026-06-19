# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

'''
Matcher engine: apply active IssueRules to a run's results, creating
ResultClassification stamps. A rule matches the History-style way - test path
(always), plus optional parameters / verdicts / tags. Tag matching is a
run-level gate (all results in a run share the run's tags).
'''

from django.core.management import call_command

from bublik.core.run.data import get_tags_by_runs
from bublik.data.models import (
    IssueRule,
    ResultClassification,
    StampOrigin,
    TestIterationResult,
)


def _result_param_dict(result):
    return {a.name: a.value for a in result.iteration.test_arguments.all()}


def _result_verdict_set(result):
    return set(
        result.meta_results.filter(meta__type='verdict').values_list('meta__value', flat=True),
    )


def rule_matches_run_tags(rule, run):
    '''Run-level tag gate. The rule's captured tags must be a subset of the
    run's tags (important or all, per the rule's flags).'''
    if not (rule.match_important_tags or rule.match_all_tags):
        return True
    important_by_run, relevant_by_run = get_tags_by_runs([run])
    important = set(important_by_run.get(run.id, []))
    captured = set(rule.tags or [])
    if rule.match_all_tags:
        all_tags = important | set(relevant_by_run.get(run.id, []))
        return captured.issubset(all_tags)
    return captured.issubset(important)


def matching_results(rule, run):
    '''Results in `run` matched by `rule` (test path + optional params/verdicts,
    gated by run-level tags).'''
    if not rule_matches_run_tags(rule, run):
        return []
    candidates = TestIterationResult.objects.filter(
        test_run=run, iteration__test_id=rule.test_id,
    ).select_related('iteration')
    matched = []
    for result in candidates:
        if rule.match_parameters and _result_param_dict(result) != (rule.parameters or {}):
            continue
        if rule.match_verdicts and _result_verdict_set(result) != set(rule.verdicts or []):
            continue
        matched.append(result)
    return matched


def apply_active_rules(run):
    '''Recompute import-origin stamps for `run` from the project's active rules.
    Idempotent: drops previous origin='import' stamps then rebuilds. Manual
    stamps (manual_apply / manual_oneoff) are never touched. Returns the number
    of stamps created.'''
    ResultClassification.objects.filter(
        result__test_run=run, origin=StampOrigin.IMPORT,
    ).delete()
    rules = IssueRule.objects.filter(project_id=run.project_id, active=True)
    created = 0
    for rule in rules:
        for result in matching_results(rule, run):
            _, made = ResultClassification.objects.get_or_create(
                result=result, rule=rule, defaults={'origin': StampOrigin.IMPORT},
            )
            created += int(made)
    return created


def runs_for_rule(rule):
    '''Distinct run ids that have stamps for this rule.'''
    return list(
        ResultClassification.objects.filter(rule=rule)
        .values_list('result__test_run_id', flat=True)
        .distinct(),
    )


def runs_for_issue(issue):
    '''Distinct run ids that have stamps for any of this issue's rules.'''
    return list(
        ResultClassification.objects.filter(rule__issue=issue)
        .values_list('result__test_run_id', flat=True)
        .distinct(),
    )


def invalidate_run_stats(run_ids):
    '''Drop cached run stats for the given runs so suppression changes show.'''
    run_ids = [r for r in run_ids if r is not None]
    if not run_ids:
        return
    args = ['run_cache', 'delete']
    for run_id in run_ids:
        args += ['-i', run_id]
    call_command(*args, '--logger_out', True)
