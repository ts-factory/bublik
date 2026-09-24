# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import contextlib
from dataclasses import asdict
from datetime import date, timedelta
import json
import logging
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.db import transaction
from rest_framework.exceptions import ValidationError

from bublik.core.comment import TestCommentService
from bublik.core.config.management import ConfigManagementService
from bublik.core.config.services import ConfigServices
from bublik.core.dashboard import DashboardService
from bublik.core.history.services import HistoryService
from bublik.core.log.services import LogService
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.project import ProjectService
from bublik.core.report.services import ReportService
from bublik.core.result import ResultService
from bublik.core.run.compromised import get_compromised_details, is_run_compromised
from bublik.core.run.services import RunService
from bublik.core.run.stats import generate_all_run_details, generate_runs_details, get_test_runs
from bublik.core.server import ServerService
from bublik.core.tree.services import TreeService
from bublik.data.models import Config, MetaTest
from bublik.data.serializers import ConfigSerializer
from bublik.interfaces.api_v2.run.serializers import (
    serialize_paginated_run_summary_results,
)
from bublik.mcp.auth import mcp_auth_required, mcp_caller
from bublik.mcp.models import JsonLog
from bublik.mcp.processor import LogProcessor
from bublik.mcp.run import _get_run_leaf_results, render_run_leaf_results, render_run_overview


if TYPE_CHECKING:
    from fastmcp import FastMCP


logger = logging.getLogger(__name__)


def get_default_date_range():
    """
    Calculate default date range: 6 months ago to today.

    Returns:
        Tuple of (from_date, to_date) as ISO format strings (yyyy-mm-dd)
    """
    to_date = date.today()

    from_date = to_date - timedelta(days=180)
    return from_date.isoformat(), to_date.isoformat()


# Return format for log tools ('markdown' or 'dict').
LOG_RETURN_FORMAT: str = 'markdown'


async def get_run_overview(
    run_id: int,
    requirements: str | None = None,
    unexpected_only: bool = False,
) -> str:
    """
    Get a complete Markdown overview of a test run.

    The overview combines run metadata (including the Project ID), status,
    conclusion, source, compromised details, and the aggregate result
    statistics tree. Each test row includes a Result ID that can be passed to
    get_run_leaf_results for concrete executions, a Test ID for the test
    comment tools, and the test's comments as `[comment_id] text`.

    Args:
        run_id: The ID of the test run
        requirements: Optional semicolon-separated requirements filter
        unexpected_only: Return only test leaves containing unexpected or
            abnormal results

    Returns:
        Markdown document containing run details and aggregate statistics
    """

    def load_overview():
        run = RunService.get_run(run_id)
        return (
            generate_all_run_details(run),
            RunService.get_run_source(run_id),
            RunService.get_run_stats(run_id, requirements),
            ReportService.get_configs_for_run_report(run),
        )

    details, source, stats, report_configs = await sync_to_async(load_overview)()

    return render_run_overview(
        details,
        source,
        stats,
        requirements,
        unexpected_only,
        report_configs=report_configs,
    )


async def get_run_leaf_results(
    leaf_result_id: int,
    requirements: str | None = None,
    results: str | None = None,
    result_properties: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    unexpected_only: bool = False,
) -> str:
    """
    Get paginated executions represented by a test leaf in a run overview.

    Pass the Result ID of a Type = 'test' row shown by get_run_overview -- not a
    'package' row (packages aggregate child tests and have no direct executions)
    and not an individual execution ID. For the common failure-investigation
    workflow, set
    unexpected_only to true to return only unexpected or abnormal executions.
    Use the advanced requirements, results, and result_properties filters to
    narrow further. requirements composes with unexpected_only; results and
    result_properties are mutually exclusive with it (they overlap with the
    unexpected/abnormal classification). With no toggle or filters, all
    executions represented by the leaf are returned.

    Args:
        leaf_result_id: Aggregate test Result ID shown by get_run_overview
        requirements: Optional semicolon-separated requirement names
        results: Optional semicolon-separated obtained result statuses
            (e.g., 'PASSED;FAILED;SKIPPED;KILLED;CORED;FAKED;INCOMPLETE')
        result_properties: Optional semicolon-separated result properties
            (e.g., 'expected;unexpected;not_run')
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)
        unexpected_only: Return only unexpected or abnormal executions.
            Can be combined with requirements; cannot be combined with
            results or result_properties

    Returns:
        Markdown table with the matching concrete executions and pagination
    """
    payload = await sync_to_async(_get_run_leaf_results)(
        leaf_result_id=leaf_result_id,
        requirements=requirements,
        results=results,
        result_properties=result_properties,
        page=page,
        page_size=page_size,
        unexpected_only=unexpected_only,
    )
    return render_run_leaf_results(payload)


async def get_result_details(result_id: int) -> dict:
    """
    Get detailed information about a test result.

    Args:
        result_id: The ID of the test result

    Returns:
        Dictionary with full result details
    """
    return await sync_to_async(ResultService.get_result_details)(result_id)


async def get_result_artifacts_and_verdicts(result_id: int) -> dict:
    """
    Get artifacts and verdicts for a test result.

    Args:
        result_id: The ID of the test result

    Returns:
        Dictionary with artifacts and verdicts lists
    """
    return await sync_to_async(ResultService.get_result_artifacts_and_verdicts)(result_id)


# Project tools


async def list_projects() -> list[dict]:
    """
    List all available projects.

    Returns:
        List of projects with id and name
    """
    return await sync_to_async(ProjectService.list_projects)()


async def get_project(project_id: int) -> dict:
    """
    Get details of a specific project.

    Args:
        project_id: The ID of the project

    Returns:
        Dictionary with project id and name
    """
    return await sync_to_async(ProjectService.get_project)(project_id)


# Runs tools


async def list_runs(  # noqa: PLR0913, PLR0917
    start_date: str | None = None,
    finish_date: str | None = None,
    project_id: int | None = None,
    run_status: str | None = None,
    run_metas: str | None = None,
    tag_expr: str | None = None,
    label_expr: str | None = None,
    revision_expr: str | None = None,
    branch_expr: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    """
    List test runs with comprehensive filtering.

    Args:
        start_date: Start date in yyyy-mm-dd format (e.g., '2024-01-01')
        finish_date: Finish date in yyyy-mm-dd format (e.g., '2024-01-31')
        project_id: Optional project ID to filter results
        run_status: Optional run status filter
        run_metas: Semicolon-separated metadata (tags, labels, revisions, branches)
        tag_expr: Tag expression filter (supports boolean logic: &, |, !)
        label_expr: Label expression filter (supports boolean logic: &, |, !)
        revision_expr: Revision expression filter (supports boolean logic: &, |, !)
        branch_expr: Branch expression filter (supports boolean logic: &, |, !)
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)

    Returns:
        Dictionary with pagination metadata and run details
    """
    queryset = await sync_to_async(RunService.list_runs_queryset)(
        start_date=start_date,
        finish_date=finish_date,
        project_id=project_id,
        run_status=run_status,
        run_metas=run_metas,
        tag_expr=tag_expr,
        label_expr=label_expr,
        revision_expr=revision_expr,
        branch_expr=branch_expr,
    )
    runs_details = await sync_to_async(generate_runs_details)(queryset)
    return serialize_paginated_run_summary_results(
        await sync_to_async(PaginatedResult.paginate_queryset)(
            runs_details,
            page,
            page_size,
        ),
    )


async def list_runs_today(
    project_id: int | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    """
    List test runs for today.

    Args:
        project_id: Optional project ID to filter results
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)

    Returns:
        Dictionary with pagination metadata and today's run details
    """
    date_str = await sync_to_async(DashboardService.get_latest_dashboard_date)(
        project_id=project_id,
    )
    queryset = (
        await sync_to_async(RunService.list_runs_by_dashboard_date_queryset)(
            date=date_str,
            project_id=project_id,
        )
        if date_str
        else []
    )
    runs_details = await sync_to_async(generate_runs_details)(queryset) if date_str else []
    return serialize_paginated_run_summary_results(
        await sync_to_async(PaginatedResult.paginate_queryset)(
            runs_details,
            page,
            page_size,
        ),
    )


async def get_latest_run_date(project_id: int | None = None) -> str | None:
    """
    Get the most recent run date.

    Args:
        project_id: Optional project ID to filter by

    Returns:
        Date string in yyyy-mm-dd format, or None if no runs exist
    """

    def _get_latest_date():
        runs = get_test_runs(order_by='-start')
        if project_id:
            runs = runs.filter(project_id=project_id)
        latest = runs.first()
        return latest.start.date().isoformat() if latest else None

    return await sync_to_async(_get_latest_date)()


# Dashboard tools


async def get_dashboard(
    date: str,
    project_id: int | None = None,
    sort_by: str | None = None,
    validate: bool = False,
) -> dict:
    """
    Get dashboard data for a specific date (structured format).

    Returns the same format as /api/v2/dashboard/ endpoint with:
    - date: The dashboard date
    - rows: List of {row_cells, context} for each run
    - header: Column definitions
    - payload: Handler descriptions

    Args:
        date: Date in yyyy-mm-dd format (e.g., '2024-01-15')
        project_id: Optional project ID to filter results
        sort_by: Optional comma-separated column keys to sort by (e.g., 'start,total')
        validate: If True, validate dashboard settings before returning
                  (useful for debugging config issues)

    Returns:
        Dictionary with dashboard structure

    Raises:
        ValidationError: if validate=True and settings are invalid
    """
    # Optional validation (useful for debugging config issues)
    if validate:
        await sync_to_async(DashboardService.validate_dashboard_settings)(
            project_id,
            raise_on_error=True,
        )

    sort_config = sort_by.split(',') if sort_by else None
    return await sync_to_async(DashboardService.get_dashboard_data)(
        date=date,
        project_id=project_id,
        sort_config=sort_config,
    )


async def get_dashboard_today(
    project_id: int | None = None,
    sort_by: str | None = None,
) -> dict:
    """
    Get dashboard data for today (structured format).

    Args:
        project_id: Optional project ID to filter results
        sort_by: Optional comma-separated column keys to sort by (e.g., 'start,total')

    Returns:
        Dictionary with the latest dashboard structure
    """
    date_str = await sync_to_async(DashboardService.get_latest_dashboard_date)(
        project_id=project_id,
    )

    if not date_str:
        return {'date': None, 'rows': [], 'header': [], 'payload': {}}

    sort_config = sort_by.split(',') if sort_by else None
    return await sync_to_async(DashboardService.get_dashboard_data)(
        date=date_str,
        project_id=project_id,
        sort_config=sort_config,
    )


async def get_latest_dashboard_date(project_id: int | None = None) -> str | None:
    """
    Get the most recent date with dashboard data.

    Args:
        project_id: Optional project ID to filter by

    Returns:
        Date string in yyyy-mm-dd format, or None if no data
    """
    return await sync_to_async(DashboardService.get_latest_dashboard_date)(
        project_id=project_id,
    )


# Log tools


async def get_log_urls(result_id: int, page: int | None = None) -> dict:
    """
    Get log URLs for a test result (without fetching content).

    Args:
        result_id: The ID of the test result
        page: Optional page number (0 for all pages combined, >0 for specific page)

    Returns:
        Dictionary with 'url' and 'attachments_url' keys
    """
    return await sync_to_async(LogService.get_json_log_urls)(
        result_id,
        page,
        request_origin=None,
    )


async def get_log_html_url(result_id: int) -> str | None:
    """
    Get HTML log URL for a test result.

    Args:
        result_id: The ID of the test result

    Returns:
        URL string or None if not available
    """
    return await sync_to_async(LogService.get_html_log_url)(result_id)


async def get_log_overview(
    result_id: int,
    page: int | None = None,
    include_scenario: bool = True,
    max_content_length: int | None = 200,
) -> dict | str:
    """
    Get structured log overview with metadata and optional scenario logs.

    Extracts comprehensive log header information including test metadata,
    parameters, verdicts, artifacts, requirements, authors, and statistics.

    Args:
        result_id: Test result ID
        page: Optional page number (0 for all pages combined, >0 for specific page)
        include_scenario: Include scenario-related log lines in overview
        max_content_length: Maximum content length per scenario line. If specified,
            content exceeding this length will be truncated and marked with
            content_truncated=True. Use get_log_line to retrieve full content.

    Returns:
        LogOverview as dictionary or markdown string

    Raises:
        ValueError: If no header block is found in the log
    """

    def _get_overview():
        log_data = LogService.get_log_json(result_id, page)
        validated_log = JsonLog.model_validate(log_data['log'])

        processor = LogProcessor(validated_log)
        overview = processor.get_overview(
            include_scenario=include_scenario,
            max_content_length=max_content_length,
        )

        if LOG_RETURN_FORMAT == 'markdown':
            return overview.to_markdown()
        return overview.model_dump()

    return await sync_to_async(_get_overview)()


async def get_log_lines(  # noqa: PLR0913, PLR0917
    result_id: int,
    page: int | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    levels: list[str] | None = None,
    entity_names: list[str] | None = None,
    user_names: list[str] | None = None,
    entity_user_pairs: list[str] | None = None,
    table_index: int = 0,
    max_content_length: int | None = 200,
) -> dict | str:
    """
    Extract and filter log lines.

    Retrieves log lines with optional range-based and content-based filtering.
    All filters are AND-combined. Supports truncating content for display.

    Args:
        result_id: Test result ID
        page: Optional page number (0 for all pages combined, >0 for specific page)
        start_line: Starting line number (inclusive), None for start
        end_line: Ending line number (inclusive), None for end
        levels: Filter by log levels (ERROR, WARN, INFO, VERB, PACKET, RING)
        entity_names: Filter by entity names
        user_names: Filter by user names
        entity_user_pairs: Filter by "entity:user" combinations (e.g., ["Tester:Run"])
        table_index: Log table block index (default: 0)
        max_content_length: Maximum content length per line. If specified,
            content exceeding this length will be truncated and marked with
            content_truncated=True. Use get_log_line to retrieve full content.

    Returns:
        LogLinesResult as dictionary or markdown string. When truncated,
        lines will have content_truncated=True and original_content_length set.
    """

    def _get_lines():
        log_data = LogService.get_log_json(result_id, page)
        validated_log = JsonLog.model_validate(log_data['log'])

        processor = LogProcessor(validated_log)
        result = processor.get_lines(
            start_line=start_line,
            end_line=end_line,
            levels=levels,
            entity_names=entity_names,
            user_names=user_names,
            entity_user_pairs=entity_user_pairs,
            table_index=table_index,
            max_content_length=max_content_length,
        )

        if LOG_RETURN_FORMAT == 'markdown':
            return result.to_markdown(max_content_length=None)
        return result.model_dump()

    return await sync_to_async(_get_lines)()


async def get_log_line(
    result_id: int,
    line_number: int,
    page: int | None = None,
) -> dict | str:
    """
    Get a single log line with full, untruncated content.

    Use this tool to retrieve the complete content of a specific line
    after using get_log_lines with truncation.

    Args:
        result_id: Test result ID
        line_number: Line number to retrieve
        page: Optional page number (0 for all pages combined, >0 for specific page)

    Returns:
        Single LogLine as dictionary or markdown string with full content

    Raises:
        ValueError: If line_number is not found
    """

    def _get_line():
        log_data = LogService.get_log_json(result_id, page)
        validated_log = JsonLog.model_validate(log_data['log'])

        processor = LogProcessor(validated_log)

        result = processor.get_lines(
            start_line=line_number,
            end_line=line_number,
            max_content_length=None,
        )

        if not result.lines:
            msg = f'Line {line_number} not found in result {result_id}'
            raise ValueError(msg)

        line = next(
            (line for line in result.lines if line.line_number == line_number),
            result.lines[0],
        )

        if LOG_RETURN_FORMAT == 'markdown':
            return line.to_markdown(max_content_length=None)
        return line.model_dump()

    return await sync_to_async(_get_line)()


async def get_tree_path(result_id: int) -> list[int]:
    """
    Get path to a specific test result in the tree.

    Args:
        result_id: The ID of the test result

    Returns:
        List of node IDs from root to the specified result
    """
    return await sync_to_async(TreeService.get_tree_path)(result_id)


# History tools


async def get_history(  # noqa: PLR0913, PLR0917
    test_name: str,
    project_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    result_statuses: str | None = None,
    branches: str | None = None,
    labels: str | None = None,
    tags: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    """
    Get test history (linear format).

    Args:
        test_name: Name of the test to get history for
        project_id: Optional project ID to filter by
        from_date: Start date in yyyy-mm-dd format (default: 6 months ago)
        to_date: End date in yyyy-mm-dd format (default: today)
        result_statuses: Semicolon-separated result statuses (e.g., 'PASSED;FAILED')
        branches: Semicolon-separated branch names
        labels: Semicolon-separated labels
        tags: Semicolon-separated tags
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)

    Returns:
        Dictionary with history results, counts, date range, and pagination
    """
    # Set default date range if not provided
    if from_date is None and to_date is None:
        from_date, to_date = get_default_date_range()

    filters = {
        'project_id': project_id,
        'from_date': from_date or '',
        'to_date': to_date or '',
        'result_statuses': result_statuses or '',
        'branches': branches or '',
        'labels': labels or '',
        'tags': tags or '',
        'page': page,
        'page_size': page_size,
    }
    return await sync_to_async(HistoryService.get_history)(test_name, **filters)


async def get_history_grouped(  # noqa: PLR0913, PLR0917
    test_name: str,
    project_id: int | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    result_statuses: str | None = None,
    branches: str | None = None,
    labels: str | None = None,
    tags: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> dict:
    """
    Get test history grouped by iteration.

    Args:
        test_name: Name of the test to get history for
        project_id: Optional project ID to filter by
        from_date: Start date in yyyy-mm-dd format (default: 6 months ago)
        to_date: End date in yyyy-mm-dd format (default: today)
        result_statuses: Semicolon-separated result statuses (e.g., 'PASSED;FAILED')
        branches: Semicolon-separated branch names
        labels: Semicolon-separated labels
        tags: Semicolon-separated tags
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)

    Returns:
        Dictionary with grouped history results, counts, date range, and pagination
    """
    # Set default date range if not provided
    if from_date is None and to_date is None:
        from_date, to_date = get_default_date_range()

    filters = {
        'project_id': project_id,
        'from_date': from_date or '',
        'to_date': to_date or '',
        'result_statuses': result_statuses or '',
        'branches': branches or '',
        'labels': labels or '',
        'tags': tags or '',
        'page': page,
        'page_size': page_size,
    }
    return await sync_to_async(HistoryService.get_history_grouped)(test_name, **filters)


# Run extension tools


async def get_run_requirements(run_id: int) -> list[str]:
    """
    Get requirements for a run.

    Args:
        run_id: The ID of the test run

    Returns:
        Sorted list of requirement strings
    """
    return await sync_to_async(RunService.get_run_requirements)(run_id)


async def get_run_comment(run_id: int) -> str | None:
    """
    Get comment for a run.

    Args:
        run_id: The ID of the test run

    Returns:
        Comment string or None if no comment exists
    """
    return await sync_to_async(RunService.get_run_comment)(run_id)


async def get_run_report_configs(run_id: int) -> list[dict]:
    """
    Get available report configurations for a run.

    The configs list being non-empty means the run has report data and a
    report page at ``/runs/{run_id}/report`` can be generated.

    Args:
        run_id: The ID of the test run

    Returns:
        List of report config dicts with id, name, description, version and project
    """
    run = await sync_to_async(RunService.get_run)(run_id)
    configs = await sync_to_async(ReportService.get_configs_for_run_report)(run)
    return [asdict(cfg) for cfg in configs]


# Server tools


async def get_server_version() -> dict:
    """
    Get server version information.

    Returns:
        Dictionary with repository revision information
    """
    return await sync_to_async(ServerService.get_version)()


# Test comment tools. A comment is identified by its meta id, as in the REST API.


def _comment_row(metatest) -> dict:
    value = metatest.meta.value
    with contextlib.suppress(ValueError, TypeError):
        value = json.loads(value)
    return {
        'comment_id': metatest.meta_id,
        'test_id': metatest.test_id,
        'project_id': metatest.project_id,
        'comment': value,
        'serial': metatest.serial,
        'updated': metatest.updated,
    }


def _comment_rows(test_id, project_id) -> list[dict]:
    comments = (
        TestCommentService.list_for_test(test_id, project_id)
        .select_related('meta')
        .order_by('serial')
    )
    return [_comment_row(metatest) for metatest in comments]


async def get_test_comments(test_id: int, project_id: int) -> list[dict]:
    """
    List the comments left on a test within a project.

    get_run_overview already shows each test's comments as `[comment_id] text`;
    use this when you need them for a test outside a run overview.

    Args:
        test_id: The test
        project_id: The project the comments belong to

    Returns:
        List of comments with comment_id, comment, serial and updated. The
        `comment_id` is what edit_test_comment takes to change or remove one.
    """
    return await sync_to_async(_comment_rows)(test_id, project_id)


@mcp_auth_required(action='manage_test_comments')
async def edit_test_comment(
    test_id: int,
    project_id: int,
    comment: str | None = None,
    comment_id: int | None = None,
) -> dict:
    """
    Add, change or remove a comment on a test, acting as the caller.

    What happens follows from the arguments: text without `comment_id` adds
    a new comment; text with `comment_id` replaces that comment (its position
    is kept, its id changes); `comment_id` without text removes it.
    `comment_id` is the id shown in get_run_overview's Comments column or by
    get_test_comments; `test_id` and `project_id` come from the same overview.

    Requires an access token and the `manage_test_comments` permission.

    Args:
        test_id: The test
        project_id: The project the comment belongs to
        comment: The comment text; omit it to remove `comment_id`
        comment_id: The comment to change or remove; omit it to add a new one

    Returns:
        The resulting comment, or `{'removed': comment_id}` after a removal
    """

    def _edit():
        has_text = bool(comment and comment.strip())
        if comment_id is None:
            if not has_text:
                msg = 'Pass the comment text to add a comment.'
                raise ValidationError(msg)
            created = TestCommentService.add(
                test_id=test_id,
                project_id=project_id,
                comment=comment,
            )
            return _comment_row(MetaTest.objects.select_related('meta').get(pk=created['id']))

        metatest = TestCommentService.get(test_id, project_id, comment_id)
        if not has_text:
            TestCommentService.delete(metatest)
            return {'removed': comment_id}
        updated = TestCommentService.update(metatest, comment)
        return _comment_row(MetaTest.objects.select_related('meta').get(pk=updated['id']))

    return await sync_to_async(_edit)()


@mcp_auth_required()
async def edit_run_comment(run_id: int, comment: str | None = None) -> dict:
    """
    Set or remove the comment on a run, acting as the caller.

    A run has at most one comment. Text replaces whatever is there, adding
    the comment when there is none; omitting the text removes the comment.
    Requires an access token.

    Args:
        run_id: The run
        comment: The comment text; omit it to remove the run's comment

    Returns:
        The run id and its resulting comment (None once removed)
    """

    def _edit():
        if comment and comment.strip():
            result = RunService.create_run_comment(run_id, comment)
            return {'run_id': run_id, 'comment': result.comment}
        # Raises only when there is no comment, which is already the goal.
        with contextlib.suppress(ValidationError):
            RunService.delete_run_comment(run_id)
        return {'run_id': run_id, 'comment': None}

    return await sync_to_async(_edit)()


@mcp_auth_required()
async def set_run_compromised(
    run_id: int,
    compromised: bool,
    comment: str | None = None,
    bug_id: str | None = None,
    reference_key: str | None = None,
) -> dict:
    """
    Mark a run as compromised, change the mark, or clear it, acting as the caller.

    `compromised=true` marks the run with `comment` (mandatory), replacing an
    existing mark; `compromised=false` clears it. To link a bug, pass `bug_id`
    and `reference_key` together: `reference_key` is a key of ISSUES in the
    project's `references` config, and the bug URL is built from that
    reference's URI plus the bug id. Requires an access token.

    Args:
        run_id: The run
        compromised: The desired state
        comment: Why the run is compromised (required when marking)
        bug_id: Optional bug identifier
        reference_key: Optional key of the bug tracker in the references config

    Returns:
        The run's compromised details: status, comment, bug_id and bug_url
    """

    def _set():
        # Atomic, so a rejected new mark keeps the old one.
        with transaction.atomic():
            if compromised:
                if is_run_compromised(run_id):
                    RunService.unmark_run_compromised(run_id)
                RunService.mark_run_compromised(run_id, comment, bug_id, reference_key)
            elif is_run_compromised(run_id):
                RunService.unmark_run_compromised(run_id)
        return get_compromised_details(run_id)

    return await sync_to_async(_set)()


# Config tools. Admin only, reads included, like the REST endpoints.


def _config_data(config: Config) -> dict:
    return dict(ConfigSerializer(config).data)


@mcp_auth_required(admin=True)
async def list_configs(project_id: int | None = None) -> dict:
    """
    List configs, one row per (project, type, name), plus what may be created.

    Requires an administrator's access token.

    Args:
        project_id: Only this project's configs; omit for every project.
            Default (no-project) configs have `project` null.

    Returns:
        `configs`: id, version, is_active, type, name, description, project,
        created (the active version, else the newest). `available_types_names`:
        the config types, and for `global` the allowed names, with whether
        each is required.
    """

    def _list():
        configs = Config.objects.all()
        if project_id is not None:
            configs = configs.filter(project_id=project_id)
        return {
            'configs': ConfigManagementService.summarize(configs),
            'available_types_names': ConfigManagementService.available_types_names(),
        }

    return await sync_to_async(_list)()


@mcp_auth_required(admin=True)
async def get_config(config_id: int) -> dict:
    """
    Get one config version with its content, and the other versions of it.

    Requires an administrator's access token.

    Args:
        config_id: The config version (an id from list_configs or `versions`)

    Returns:
        The config (id, type, name, project, version, is_active, description,
        user, content) plus `versions`: every version of the same config with
        id, version, is_active, description and created
    """

    def _get():
        config = ConfigManagementService.get(config_id)
        data = _config_data(config)
        data['versions'] = list(
            Config.objects.get_all_versions(config.type, config.name, config.project).values(
                'id',
                'version',
                'is_active',
                'description',
                'created',
            ),
        )
        return data

    return await sync_to_async(_get)()


@mcp_auth_required(admin=True)
async def get_config_schema(config_type: str, config_name: str | None = None) -> dict:
    """
    The JSON schema a config's content must satisfy.

    Requires an administrator's access token. Fetch it before writing content:
    edit_config validates content against it.

    Args:
        config_type: `global`, `report` or `schedule`
        config_name: For `global` configs, the name (e.g. `per_conf`)

    Returns:
        The JSON schema
    """

    def _schema():
        schema = ConfigServices.get_schema(config_type, config_name)
        if schema is None:
            msg = (
                f'There is no JSON schema for config type {config_type!r}, name {config_name!r}'
            )
            raise ValidationError(msg)
        return schema

    return await sync_to_async(_schema)()


@mcp_auth_required(admin=True)
async def edit_config(
    config_id: int | None = None,
    config_type: str | None = None,
    name: str | None = None,
    project_id: int | None = None,
    content: dict | str | None = None,
    description: str | None = None,
    is_active: bool | None = None,
) -> dict:
    """
    Create a config, or change an existing one, acting as the caller.

    Without `config_id` a config is created: `config_type`, `name` and
    `content` are required, `project_id` omitted means the default
    (no-project) config, and it becomes the active version unless
    `is_active` is false. With `config_id` only the arguments given change:
    new `content` creates a new version (attributed to the caller) and
    activates it if the edited version was active, while content equal to an
    existing version switches to that version; `is_active` alone activates or
    deactivates the version; `name` renames every version. Fetch the current
    content with get_config first and pass the whole edited content back:
    content is replaced, not merged, and validated against get_config_schema.
    Removing a version is a separate, irreversible tool: delete_config.

    Requires an administrator's access token.

    Args:
        config_id: The config version to change; omit it to create a config
        config_type: `global`, `report` or `schedule` (creation only)
        name: The config name; for `global` one of the names in list_configs
        project_id: The project of a new config; omit for the default config
        content: The config content, as a JSON object or a JSON string
        description: A short description
        is_active: Whether this version is the active one

    Returns:
        The resulting config version, with `created_new_version`
    """

    def _edit():
        user = mcp_caller()
        if config_id is None:
            required = {'config_type': config_type, 'name': name, 'content': content}
            missing = [key for key, value in required.items() if value is None]
            if missing:
                msg = f'Creating a config requires {", ".join(missing)}.'
                raise ValidationError(msg)
            data = {
                'type': config_type,
                'name': name,
                'project': project_id,
                'description': description or '',
                'is_active': True if is_active is None else is_active,
                'content': content,
            }
            config = ConfigManagementService.create(data, user)
            return {**_config_data(config), 'created_new_version': True}

        if config_type is not None or project_id is not None:
            msg = (
                'The type and project of an existing config cannot be changed; '
                'create a new config instead.'
            )
            raise ValidationError(msg)
        changes = {
            'content': content,
            'description': description,
            'is_active': is_active,
            'name': name,
        }
        data = {key: value for key, value in changes.items() if value is not None}
        config = ConfigManagementService.get(config_id)
        config, created = ConfigManagementService.update(config, data, user)
        return {**_config_data(config), 'created_new_version': created}

    return await sync_to_async(_edit)()


@mcp_auth_required(admin=True)
async def delete_config(config_id: int) -> dict:
    """
    Delete one config version. Irreversible.

    Requires an administrator's access token. Other versions of the same
    config are kept; delete them one by one if that is what is wanted. To
    retire a version without losing it, deactivate it with edit_config.

    Args:
        config_id: The config version to delete

    Returns:
        The id of the deleted config version
    """

    def _delete():
        ConfigManagementService.delete(ConfigManagementService.get(config_id), mcp_caller())
        return {'deleted': config_id}

    return await sync_to_async(_delete)()


# Project tools that write. Admin only, like the REST endpoints.


@mcp_auth_required(admin=True)
async def edit_project(name: str, project_id: int | None = None) -> dict:
    """
    Create a project, or rename an existing one, acting as the caller.

    Without `project_id` a project called `name` is created; with it, that
    project is renamed. Project names are unique. Requires an administrator's
    access token.

    Args:
        name: The project name
        project_id: The project to rename; omit it to create a project

    Returns:
        The resulting project: id and name
    """

    def _edit():
        if project_id is None:
            project = ProjectService.create_project({'name': name})
        else:
            project = ProjectService.update_project(project_id, {'name': name}, partial=True)
        return ProjectService.get_project(project.id)

    return await sync_to_async(_edit)()


@mcp_auth_required(admin=True)
async def delete_project(project_id: int) -> dict:
    """
    Delete a project. Irreversible.

    Refused while any run belongs to the project. Requires an administrator's
    access token.

    Args:
        project_id: The project to delete

    Returns:
        The id of the deleted project
    """

    def _delete():
        ProjectService.delete_project(project_id)
        return {'deleted': project_id}

    return await sync_to_async(_delete)()


# Shared registry of Bublik tool callables. Consumed both by the FastMCP HTTP
# server (see register_tools) and directly by the in-process chat agent
# (see bublik.ai.agent), so both expose exactly the same tools.
MCP_TOOLS = [
    get_run_overview,
    get_run_leaf_results,
    get_result_details,
    get_result_artifacts_and_verdicts,
    list_projects,
    get_project,
    list_runs,
    list_runs_today,
    get_latest_run_date,
    get_dashboard,
    get_dashboard_today,
    get_latest_dashboard_date,
    get_log_urls,
    get_log_html_url,
    get_log_overview,
    get_log_lines,
    get_log_line,
    get_tree_path,
    get_history,
    get_history_grouped,
    get_run_requirements,
    get_run_comment,
    get_run_report_configs,
    get_server_version,
    get_test_comments,
]


# Tools that write; each requires a caller.
MCP_WRITE_TOOLS = [
    edit_test_comment,
    edit_run_comment,
    set_run_compromised,
]

# Tools only an administrator may call, reads included.
MCP_ADMIN_TOOLS = [
    list_configs,
    get_config,
    get_config_schema,
    edit_config,
    delete_config,
    edit_project,
    delete_project,
]

# Reads showing data that the write tools, the browser or the chat can change.
# Never served from the response cache.
MCP_UNCACHED_TOOLS = [
    get_run_overview,
    list_projects,
    get_project,
    list_runs,
    list_runs_today,
    get_dashboard,
    get_dashboard_today,
    get_history,
    get_history_grouped,
    get_run_comment,
    get_run_report_configs,
    get_test_comments,
]

MCP_WRITE_TOOL_NAMES = [tool.__name__ for tool in MCP_WRITE_TOOLS]
MCP_ADMIN_TOOL_NAMES = [tool.__name__ for tool in MCP_ADMIN_TOOLS]
MCP_UNCACHED_TOOL_NAMES = [tool.__name__ for tool in MCP_UNCACHED_TOOLS]


def register_tools(mcp: FastMCP) -> None:
    """
    Register all shared Bublik tools with the FastMCP server.
    """
    for tool in MCP_TOOLS:
        mcp.tool(tool)
    for tool in MCP_WRITE_TOOLS:
        mcp.tool(tool, tags={'write'})
    for tool in MCP_ADMIN_TOOLS:
        mcp.tool(tool, tags={'write', 'admin'})
