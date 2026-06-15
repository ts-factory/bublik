# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from bublik.core.run.dto import (
        MarkRunCompromisedResult,
        RunCommentResult,
        RunCompromisedDetails,
        RunDetailsResult,
        RunRevision,
        RunSpecialCategory,
        RunStatsComment,
        RunStatsResult,
        RunStatsValues,
        RunSummaryResult,
        RunSummaryStats,
    )


def serialize_run_compromised_details(
    compromised: RunCompromisedDetails | None,
):
    if compromised is None:
        return None

    return {
        'status': compromised.status,
        'comment': compromised.comment,
        'bug_id': compromised.bug_id,
        'bug_url': compromised.bug_url,
    }


def serialize_run_revision(revision: RunRevision):
    return {
        'name': revision.name,
        'value': revision.value,
        'url': revision.url,
    }


def serialize_run_special_categories(categories: list[RunSpecialCategory]):
    return {category.name: category.values for category in categories}


def serialize_run_details(run_details: RunDetailsResult):
    return {
        'project_id': run_details.project_id,
        'project_name': run_details.project_name,
        'id': run_details.id,
        'start': run_details.start,
        'finish': run_details.finish,
        'duration': run_details.duration,
        'main_package': run_details.main_package,
        'status': run_details.status,
        'status_by_nok': run_details.status_by_nok,
        'compromised': serialize_run_compromised_details(run_details.compromised),
        'conclusion': run_details.conclusion,
        'conclusion_reason': run_details.conclusion_reason,
        'important_tags': run_details.important_tags,
        'relevant_tags': run_details.relevant_tags,
        'branches': run_details.branches,
        'revisions': [serialize_run_revision(revision) for revision in run_details.revisions],
        'labels': run_details.labels,
        'special_categories': serialize_run_special_categories(
            run_details.special_categories,
        ),
        'configuration': run_details.configuration,
    }


def serialize_run_stats_values(stats: RunStatsValues):
    return {
        'passed': stats.passed,
        'failed': stats.failed,
        'passed_unexpected': stats.passed_unexpected,
        'failed_unexpected': stats.failed_unexpected,
        'skipped': stats.skipped,
        'skipped_unexpected': stats.skipped_unexpected,
        'abnormal': stats.abnormal,
    }


def serialize_run_stats_comment(comment: RunStatsComment):
    return {
        'comment_id': comment.comment_id,
        'updated': comment.updated,
        'serial': comment.serial,
        'comment': comment.comment,
    }


def serialize_run_stats_result(stats: RunStatsResult | None):
    if stats is None:
        return None

    return {
        'result_id': stats.result_id,
        'exec_seqno': stats.exec_seqno,
        'parent_id': stats.parent_id,
        'type': stats.type,
        'test_id': stats.test_id,
        'test_name': stats.test_name,
        'period': stats.period,
        'path': stats.path,
        'objective': stats.objective,
        'children': [serialize_run_stats_result(child) for child in stats.children],
        'stats': serialize_run_stats_values(stats.stats),
        'comments': [serialize_run_stats_comment(comment) for comment in stats.comments],
    }


def serialize_mark_run_compromised_result(result: MarkRunCompromisedResult):
    return {
        'comment': result.comment,
        'bug': result.bug,
    }


def serialize_run_summary_stats(stats: RunSummaryStats | None):
    if stats is None:
        return None

    return {
        'tests_total': stats.tests_total,
        'tests_total_plan_percent': stats.tests_total_plan_percent,
        'tests_total_ok': stats.tests_total_ok,
        'tests_total_ok_percent': stats.tests_total_ok_percent,
        'tests_total_nok': stats.tests_total_nok,
        'tests_total_nok_percent': stats.tests_total_nok_percent,
    }


def serialize_run_summary_result(result: RunSummaryResult):
    return {
        'id': result.id,
        'project_id': result.project_id,
        'project_name': result.project_name,
        'start': result.start,
        'finish': result.finish,
        'duration': result.duration,
        'status': result.status,
        'status_by_nok': result.status_by_nok,
        'compromised': result.compromised,
        'conclusion': result.conclusion,
        'conclusion_reason': result.conclusion_reason,
        'metadata': result.metadata,
        'important_tags': result.important_tags,
        'relevant_tags': result.relevant_tags,
        'stats': serialize_run_summary_stats(result.stats),
    }


def serialize_run_summary_results(results: list[RunSummaryResult]):
    return [serialize_run_summary_result(result) for result in results]


def serialize_paginated_run_summary_results(paginated_result):
    paginated_result['results'] = serialize_run_summary_results(
        paginated_result['results'],
    )
    return paginated_result


def serialize_run_comment_result(result: RunCommentResult):
    return {
        'id': result.id,
        'comment': result.comment,
    }
