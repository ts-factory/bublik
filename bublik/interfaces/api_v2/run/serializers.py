# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from rest_framework import serializers


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


class PaginationSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class RunListQuerySerializer(serializers.Serializer):
    start_date = serializers.DateField(required=False)
    finish_date = serializers.DateField(required=False)
    project = serializers.IntegerField(required=False)
    run_status = serializers.CharField(required=False)
    run_metas = serializers.CharField(required=False)
    tag_expr = serializers.CharField(required=False)
    label_expr = serializers.CharField(required=False)
    revision_expr = serializers.CharField(required=False)
    branch_expr = serializers.CharField(required=False)


class RunChartsQuerySerializer(serializers.Serializer):
    group_by = serializers.ChoiceField(
        choices=('day', 'week'),
        required=False,
    )


class DropCacheRequestSerializer(serializers.Serializer):
    keys = serializers.ListField(child=serializers.CharField())


class DropCacheResponseSerializer(serializers.Serializer):
    results = serializers.ListField(child=serializers.IntegerField())


class CompromisedDetailsSerializer(serializers.Serializer):
    status = serializers.BooleanField()
    comment = serializers.CharField(allow_null=True, required=False)
    bug_id = serializers.CharField(allow_null=True, required=False)
    bug_url = serializers.CharField(allow_null=True, required=False)


class RevisionSerializer(serializers.Serializer):
    name = serializers.CharField()
    value = serializers.CharField()
    url = serializers.CharField(allow_blank=True)


class RunSummaryStatsSerializer(serializers.Serializer):
    tests_total = serializers.IntegerField()
    tests_total_plan_percent = serializers.IntegerField(allow_null=True)
    tests_total_ok = serializers.IntegerField()
    tests_total_ok_percent = serializers.IntegerField()
    tests_total_nok = serializers.IntegerField()
    tests_total_nok_percent = serializers.IntegerField()


class RunListItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    start = serializers.DateTimeField(allow_null=True)
    finish = serializers.DateTimeField(allow_null=True)
    duration = serializers.DurationField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    status_by_nok = serializers.CharField()
    compromised = serializers.BooleanField(allow_null=True)
    conclusion = serializers.CharField()
    conclusion_reason = serializers.CharField(allow_null=True)
    metadata = serializers.ListField(child=serializers.CharField())
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())
    stats = RunSummaryStatsSerializer(allow_null=True)


class RunDetailsResponseSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    id = serializers.IntegerField()
    start = serializers.DateTimeField(allow_null=True)
    finish = serializers.DateTimeField(allow_null=True)
    duration = serializers.DurationField(allow_null=True)
    main_package = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    status_by_nok = serializers.CharField()
    compromised = CompromisedDetailsSerializer(allow_null=True)
    conclusion = serializers.CharField()
    conclusion_reason = serializers.CharField(allow_null=True)
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())
    branches = serializers.ListField(child=serializers.CharField())
    revisions = RevisionSerializer(many=True)
    labels = serializers.ListField(child=serializers.CharField())
    special_categories = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
    )
    configuration = serializers.CharField(allow_null=True)


class RunStatsQuerySerializer(serializers.Serializer):
    requirements = serializers.CharField(required=False)


class RunStatsValuesSerializer(serializers.Serializer):
    passed = serializers.IntegerField()
    failed = serializers.IntegerField()
    passed_unexpected = serializers.IntegerField()
    failed_unexpected = serializers.IntegerField()
    skipped = serializers.IntegerField()
    skipped_unexpected = serializers.IntegerField()
    abnormal = serializers.IntegerField()


class RunStatsCommentSerializer(serializers.Serializer):
    comment_id = serializers.CharField()
    updated = serializers.CharField()
    serial = serializers.CharField()
    comment = serializers.CharField()


class RunStatsNodeSerializer(serializers.Serializer):
    result_id = serializers.IntegerField()
    exec_seqno = serializers.IntegerField()
    parent_id = serializers.IntegerField(allow_null=True)
    type = serializers.CharField()
    test_id = serializers.IntegerField()
    test_name = serializers.CharField()
    period = serializers.CharField()
    path = serializers.ListField(child=serializers.CharField())
    objective = serializers.CharField(allow_blank=True)
    children = serializers.ListField(
        child=serializers.DictField(),
        help_text='Child nodes with the same structure.',
    )
    stats = RunStatsValuesSerializer()
    comments = RunStatsCommentSerializer(many=True)


class RunStatsResponseSerializer(serializers.Serializer):
    results = RunStatsNodeSerializer(allow_null=True)
    default_columns = serializers.ListField(child=serializers.CharField())


class RunRequirementsResponseSerializer(serializers.Serializer):
    requirements = serializers.ListField(child=serializers.CharField())


class RunSourceResponseSerializer(serializers.Serializer):
    url = serializers.CharField(allow_null=True)


class RunStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField(allow_null=True)


class MarkRunCompromisedRequestSerializer(serializers.Serializer):
    comment = serializers.CharField()
    bug_id = serializers.CharField(required=False, allow_null=True)
    reference_key = serializers.CharField(required=False, allow_null=True)


class MarkRunCompromisedResponseSerializer(serializers.Serializer):
    comment = serializers.CharField()
    bug = serializers.CharField(allow_null=True)


class UnmarkRunCompromisedResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class RunCommentRequestSerializer(serializers.Serializer):
    comment = serializers.CharField()


class RunCommentValueResponseSerializer(serializers.Serializer):
    comment = serializers.CharField(allow_null=True)


class RunCommentResponseSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    comment = serializers.CharField()


class RunChartTestsSerializer(serializers.Serializer):
    ok = serializers.IntegerField()
    nok = serializers.IntegerField()
    total = serializers.IntegerField()
    passrate = serializers.FloatField()


class RunIdsByStatusSerializer(serializers.Serializer):
    run_ok = serializers.ListField(
        source='run-ok',
        child=serializers.IntegerField(),
    )
    run_running = serializers.ListField(
        source='run-running',
        child=serializers.IntegerField(),
    )
    run_warning = serializers.ListField(
        source='run-warning',
        child=serializers.IntegerField(),
    )
    run_error = serializers.ListField(
        source='run-error',
        child=serializers.IntegerField(),
    )
    run_stopped = serializers.ListField(
        source='run-stopped',
        child=serializers.IntegerField(),
    )
    run_busy = serializers.ListField(
        source='run-busy',
        child=serializers.IntegerField(),
    )
    run_compromised = serializers.ListField(
        source='run-compromised',
        child=serializers.IntegerField(),
    )
    run_interrupted = serializers.ListField(
        source='run-interrupted',
        child=serializers.IntegerField(),
    )


class RunChartBucketSerializer(serializers.Serializer):
    date = serializers.DateTimeField()
    tests = RunChartTestsSerializer()
    run_ids_by_status = RunIdsByStatusSerializer()


class RunChartsResponseSerializer(serializers.Serializer):
    buckets = RunChartBucketSerializer(many=True)


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
