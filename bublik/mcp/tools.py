# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import contextlib
from dataclasses import asdict
from datetime import date, timedelta
from functools import wraps
import json
import logging
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.datastructures import MultiValueDict
from fastmcp.exceptions import ToolError
from rest_framework.exceptions import APIException, ValidationError

from bublik.core.auth import actions_permitted_somewhere, is_admin
from bublik.core.classification import DISPOSITION_TO_EXPECTED
from bublik.core.comment import TestCommentService
from bublik.core.config.management import ConfigManagementService
from bublik.core.config.services import ConfigServices
from bublik.core.dashboard import DashboardService
from bublik.core.history.services import HistoryService
from bublik.core.issue import (
    IssueManagementService,
    IssueRuleManagementService,
)
from bublik.core.issue_results import IssueResultsFilters, IssueResultsService
from bublik.core.log.services import LogService
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.project import ProjectService
from bublik.core.project.dto import (
    ProjectDTO,  # noqa: TC001 - resolved eagerly by fastmcp/pydantic at @mcp.tool() registration time, not lazily
)
from bublik.core.report.services import ReportService
from bublik.core.result import ResultService
from bublik.core.result.services import UNSET
from bublik.core.run.classification import ClassificationService
from bublik.core.run.services import RunService
from bublik.core.run.stats import (
    generate_all_run_details,
    generate_results_details,
    generate_runs_details,
)
from bublik.core.server import ServerService
from bublik.core.server.dto import RepoRevisionsDTO  # noqa: TC001
from bublik.core.tree.services import TreeService
from bublik.data.serializers import ConfigSerializer, IssueRuleSerializer, IssueSerializer
from bublik.interfaces.api_v2.history.serializers import (
    HistoryGroupedResponseSerializer,
    HistoryListResponseSerializer,
)
from bublik.interfaces.api_v2.issue.serializers import (
    IssueResultsFacetsSerializer,
    IssueResultsQuerySerializer,
)
from bublik.interfaces.api_v2.log.serializers import LogJsonResponseSerializer
from bublik.interfaces.api_v2.result.serializers import (
    ClassifyOptionsRequestSerializer,
    ClassifyOptionsRuleSerializer,
    ClassifyRequestSerializer,
)
from bublik.interfaces.api_v2.run.serializers import (
    RunIssueSummarySerializer,
    serialize_paginated_run_summary_results,
)
from bublik.mcp import docs
from bublik.mcp.auth import authorize_action, mcp_auth_required, mcp_caller
from bublik.mcp.models import JsonLog
from bublik.mcp.processor import LogProcessor
from bublik.mcp.run import _get_run_leaf_results, render_run_leaf_results, render_run_overview


if TYPE_CHECKING:
    from fastmcp import FastMCP


logger = logging.getLogger(__name__)

MANAGE_ISSUES = 'manage_issues'
MANAGE_TEST_COMMENTS = 'manage_test_comments'


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


async def get_run_leaf_results(  # noqa: PLR0913, PLR0917
    leaf_result_id: int,
    requirements: str | None = None,
    results: str | None = None,
    result_properties: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    unexpected_only: bool = False,
    issue: str | None = None,
    categories: str | None = None,
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
        issue: Semicolon-separated issue IDs; keeps the executions
            classified under any of them
        categories: Semicolon-separated classification categories
            (e.g., 'known-issue;flaky'); keeps the executions stamped by a
            rule of any of them (of the given issues, when issue is passed)

    Returns:
        Markdown table with the matching concrete executions and pagination.
        Classification tells whether a result counts as unexpected once its
        classification is applied; Issues lists the stamps it carries, each
        with the issue, the rule's category and disposition, and the stamp id
        that unclassify_results takes.
    """
    payload = await sync_to_async(_get_run_leaf_results)(
        leaf_result_id=leaf_result_id,
        requirements=requirements,
        results=results,
        result_properties=result_properties,
        page=page,
        page_size=page_size,
        unexpected_only=unexpected_only,
        issue=issue,
        categories=categories,
    )
    return render_run_leaf_results(payload)


async def get_result_details(result_id: int) -> dict:
    """
    Get detailed information about a test result.

    Args:
        result_id: The ID of the test result

    Returns:
        Dictionary with full result details: the obtained result with its
        verdicts, the expected results, the artifacts, and `issues`: the stamps
        the result carries, each with the issue, the rule's category and
        disposition and the stamp id (rule_result_id), and
        `effective_expected`: whether the result counts as expected once an
        expected classification is applied
    """
    return await sync_to_async(ResultService.get_result_details)(result_id)


# Project tools


async def list_projects() -> list[ProjectDTO]:
    """
    List all available projects.

    Returns:
        List of ProjectDTOs: the id and name of each project
    """
    return await sync_to_async(ProjectService.list_projects)()


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


async def get_latest_run_date(project_id: int | None = None) -> str | None:
    """
    Get the most recent run date.

    Args:
        project_id: Optional project ID to filter by

    Returns:
        Date string in yyyy-mm-dd format, or None if no runs exist
    """

    return await sync_to_async(RunService.get_latest_uncompromised_run_date)(project_id)


# Dashboard tools


async def get_dashboard(
    date: str | None = None,
    project_id: int | None = None,
    sort_by: str | None = None,
    validate: bool = False,
) -> dict:
    """
    Get dashboard data for a date, by default the latest one with runs.

    Returns the same format as /api/v2/dashboard/ endpoint with:
    - date: The dashboard date (None when there are no runs at all)
    - rows: List of {row_cells, context} for each run
    - header: Column definitions
    - payload: Handler descriptions

    Args:
        date: Date in yyyy-mm-dd format (e.g., '2024-01-15'); omit it for
            the most recent date that has runs
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

    if date is None:
        date = await sync_to_async(DashboardService.get_latest_dashboard_date)(
            project_id=project_id,
        )
        if not date:
            return {'date': None, 'rows': [], 'header': [], 'payload': {}}

    sort_config = sort_by.split(',') if sort_by else None
    return await sync_to_async(DashboardService.get_dashboard_data)(
        date=date,
        project_id=project_id,
        sort_config=sort_config,
    )


# Log tools


async def get_log_urls(result_id: int, page: int | None = None) -> dict:
    """
    Get log URLs for a test result (without fetching content).

    Args:
        result_id: The ID of the test result
        page: Optional page number (0 for all pages combined, >0 for specific page)

    Returns:
        Dictionary with 'url' and 'attachments_url' (the JSON log and its
        attachments) and 'html_url' (the HTML log to open in a browser, None
        if not available)
    """

    def _urls():
        urls = LogService.get_json_log_urls(result_id, page, request_origin=None)
        return {
            **LogJsonResponseSerializer(urls).data,
            'html_url': LogService.get_html_log_url(result_id),
        }

    return await sync_to_async(_urls)()


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
            content_truncated=True. Use get_log_lines without a limit to
            retrieve the full content of a line.

    Returns:
        LogOverview as dictionary or markdown string

    Raises:
        ValueError: If no header block is found in the log
    """

    def _get_overview():
        log_data = LogService.get_log_json(result_id, page)
        validated_log = JsonLog.model_validate(log_data.log)

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
    All filters are AND-combined. Content is truncated for display: to read
    one line in full, pass its number as both start_line and end_line with
    max_content_length null.

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
        max_content_length: Maximum content length per line (default: 200).
            Content exceeding it is truncated and marked with
            content_truncated=True; null returns the lines in full.

    Returns:
        LogLinesResult as dictionary or markdown string. When truncated,
        lines will have content_truncated=True and original_content_length set.
    """

    def _get_lines():
        log_data = LogService.get_log_json(result_id, page)
        validated_log = JsonLog.model_validate(log_data.log)

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
    categories: str | None = None,
    issue: str | None = None,
    explained: bool | None = None,
    untriaged: bool | None = None,
    page: int | None = None,
    page_size: int | None = None,
    group_by_iteration: bool = False,
) -> dict:
    """
    Get test history, result by result or grouped by iteration.

    Args:
        test_name: Name of the test to get history for
        project_id: Optional project ID to filter by
        from_date: Start date in yyyy-mm-dd format (default: 6 months ago)
        to_date: End date in yyyy-mm-dd format (default: today)
        result_statuses: Semicolon-separated result statuses (e.g., 'PASSED;FAILED')
        branches: Semicolon-separated branch names
        labels: Semicolon-separated labels
        tags: Semicolon-separated tags
        categories: Semicolon-separated classification categories; keeps the
            results stamped by a rule of any of them (e.g., 'known-issue;flaky')
        issue: Semicolon-separated issue IDs; keeps the results classified under them
        explained: true keeps only the results with a classification
        untriaged: true keeps only the failed results without any classification
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)
        group_by_iteration: true groups the results by iteration (the
            parameters) and, under it, by obtained result and verdicts,
            which shows how each iteration behaves across runs

    Returns:
        Dictionary with history results, counts, date range, and pagination.
        Each result has its result_id and run_id and lists its issues: the
        stamps it carries, with the issue, the rule's category, the
        disposition and the stamp id (rule_result_id). The result ids are
        what get_classify_options and classify_results take, also for
        results of several runs at once. Grouped, the results are under
        each iteration and verdict group, in results_data.
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
        'categories': categories or '',
        'issue': issue or '',
        'explained': 'true' if explained else '',
        'untriaged': 'true' if untriaged else '',
        'page': page,
        'page_size': page_size,
    }
    if group_by_iteration:
        data = await sync_to_async(HistoryService.get_history_grouped)(test_name, **filters)
        return dict(HistoryGroupedResponseSerializer(data).data)
    data = await sync_to_async(HistoryService.get_history)(test_name, **filters)
    return dict(HistoryListResponseSerializer(data).data)


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


async def get_server_version() -> RepoRevisionsDTO:
    """
    Get server version information.

    Returns:
        RepoRevisionsDTO with repository revision information
    """
    return await sync_to_async(ServerService.get_version)()


# Documentation tools


async def search_docs(query: str, limit: int = 10, scope: str = 'docs') -> dict:
    """
    Search the Bublik documentation for pages matching a query.

    Use this when the question is about Bublik itself rather than about test
    data: how a feature or page works, project and report configuration,
    deployment and Docker setup, importing logs, management commands, MCP or
    chat setup, or what changed in a release. Follow up with ``get_doc`` to
    read the matching page or section, and cite the returned ``url``.

    Args:
        query: Free-text search terms (plain words, setting names, identifiers)
        limit: Maximum number of pages to return (1-50)
        scope: 'docs' for the user guide (default), 'blog' for release notes,
            'all' for both

    Returns:
        Dictionary with ranked ``hits`` (path, title, best matching heading,
        anchor, snippet, score, url pointing at that heading), the number of
        pages searched, and a ``message`` when nothing matched or documentation
        is not deployed
    """
    return await sync_to_async(docs.search_docs)(query=query, limit=limit, scope=scope)


async def get_doc(path: str, heading: str | None = None) -> dict:
    """
    Read one documentation page, or one section of it, as Markdown.

    Args:
        path: Page path as returned by ``search_docs`` or ``list_docs``,
            e.g. ``configuration/mcp`` (the page ``url``, a leading ``/docs/``
            or a ``.md`` suffix is accepted too)
        heading: Optional section heading (text or anchor) to return only that
            section and its subsections instead of the whole page

    Returns:
        Dictionary with path, title, url, the page's headings and ``markdown``,
        or a ``message`` when documentation is not deployed
    """
    return await sync_to_async(docs.get_doc)(path=path, heading=heading)


async def list_docs(scope: str = 'docs') -> dict:
    """
    List the documentation table of contents.

    Args:
        scope: 'docs' for the user guide (default), 'blog' for release notes
            (newest first), 'all' for both

    Returns:
        Dictionary with ``pages`` (path, title, summary, category, url) and a
        ``message`` when documentation is not deployed
    """
    return await sync_to_async(docs.list_docs)(scope=scope)


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
    comments = TestCommentService.list_for_test(test_id, project_id)
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
        `comment_id` is what edit_test_comment takes to change one and
        delete_test_comment to remove it.
    """
    return await sync_to_async(_comment_rows)(test_id, project_id)


def _comment_text(comment: str | None) -> str:
    """The text of a comment, which a blank one is refused as."""
    if not (comment and comment.strip()):
        msg = 'Pass the comment text; a comment is removed by its delete tool.'
        raise ValidationError({'comment': msg})
    return comment


@mcp_auth_required(action=MANAGE_TEST_COMMENTS)
async def edit_test_comment(
    test_id: int,
    project_id: int,
    comment: str,
    comment_id: int | None = None,
) -> dict:
    """
    Add a comment to a test, or change one, acting as the caller.

    Without `comment_id` the text is added as a new comment; with it, the
    text replaces that comment (its position is kept, its id changes).
    `comment_id` is the id shown in get_run_overview's Comments column or by
    get_test_comments; `test_id` and `project_id` come from the same overview.
    To remove a comment use delete_test_comment.

    Requires an access token and the `manage_test_comments` permission.

    Args:
        test_id: The test
        project_id: The project the comment belongs to
        comment: The comment text, not blank
        comment_id: The comment to change; omit it to add a new one

    Returns:
        The resulting comment
    """

    def _edit():
        text = _comment_text(comment)
        if comment_id is None:
            return _comment_row(
                TestCommentService.add(test_id=test_id, project_id=project_id, comment=text),
            )
        metatest = TestCommentService.get(test_id, project_id, comment_id)
        return _comment_row(TestCommentService.update(metatest, text))

    return await sync_to_async(_edit)()


@mcp_auth_required(action=MANAGE_TEST_COMMENTS)
async def delete_test_comment(test_id: int, project_id: int, comment_id: int) -> dict:
    """
    Remove a comment from a test for good, acting as the caller.

    Requires an access token and the `manage_test_comments` permission.

    Args:
        test_id: The test
        project_id: The project the comment belongs to
        comment_id: The comment, as get_run_overview or get_test_comments show it

    Returns:
        `{'removed': comment_id}`
    """

    def _delete():
        TestCommentService.delete(TestCommentService.get(test_id, project_id, comment_id))
        return {'removed': comment_id}

    return await sync_to_async(_delete)()


@mcp_auth_required()
async def edit_run_comment(run_id: int, comment: str) -> dict:
    """
    Set the comment on a run, acting as the caller.

    A run has at most one comment: the text replaces the one there is, or is
    added when there is none. To remove the comment use delete_run_comment.
    Requires an access token.

    Args:
        run_id: The run
        comment: The comment text, not blank

    Returns:
        The run id and its comment
    """

    def _edit():
        result = RunService.create_run_comment(run_id, _comment_text(comment))
        return {'run_id': run_id, 'comment': result.comment}

    return await sync_to_async(_edit)()


@mcp_auth_required()
async def delete_run_comment(run_id: int) -> dict:
    """
    Remove the comment of a run for good, acting as the caller.

    Requires an access token.

    Args:
        run_id: The run

    Returns:
        The run id and `comment: None`; refused when the run has no comment
    """

    def _delete():
        RunService.delete_run_comment(run_id)
        return {'run_id': run_id, 'comment': None}

    return await sync_to_async(_delete)()


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

    return await sync_to_async(RunService.set_run_compromised)(
        run_id,
        compromised,
        comment,
        bug_id,
        reference_key,
    )


# Config tools. Admin only, reads included. The REST API is laxer with the
# reads: a project may open its configs with `read_configs`, and the schema
# needs no login.


def _config_data(config) -> dict:
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
        configs = ConfigManagementService.list_configs(project_id)
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
        data['versions'] = ConfigManagementService.versions(config)
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
        return asdict(project)

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


# Classification tools. Issues, their rules and the stamps on results, as the
# REST API shows them.


def _issue_data(issue) -> dict:
    return dict(IssueSerializer(issue).data)


def _rule_data(rule) -> dict:
    return dict(IssueRuleSerializer(rule).data)


def _rules_data(rules) -> list[dict]:
    # Serialized as a list, so the test paths are resolved for all of them at once.
    return [dict(row) for row in IssueRuleSerializer(rules, many=True).data]


def _query(given: dict) -> MultiValueDict:
    """
    Tool arguments as the query parameters a FilterSet reads: what was not
    passed is left out, and a list is a repeated parameter.
    """
    query = MultiValueDict()
    for name, value in given.items():
        if value in (None, '', []):
            continue
        query.setlist(name, value if isinstance(value, list) else [value])
    return query


def _ordering(ordering: str | None, fields) -> str | None:
    """
    The ordering a list tool was asked for. An unknown field is refused here:
    the services leave one out, as the REST `ordering` parameter does, and a
    model would take the default order for the one it asked for.
    """
    if ordering is not None and ordering.removeprefix('-') not in fields:
        msg = f"Expected any of: {', '.join(fields)}, with '-' for descending."
        raise ValidationError({'ordering': msg})
    return ordering


def _paginated_result_details(queryset, page, page_size) -> dict:
    paged = PaginatedResult.paginate_queryset(queryset, page, page_size)
    return {
        'pagination': paged['pagination'],
        'results': generate_results_details(paged['results']),
    }


async def list_issues(  # noqa: PLR0913, PLR0917
    project_id: int | None = None,
    state: str | None = None,
    category: str | None = None,
    rules: str | None = None,
    search: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    ordering: str | None = None,
    created_after: str | None = None,
    created_before: str | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
    test_name: str | None = None,
    facets: bool = False,
) -> dict:
    """
    List the issues results are classified under, newest first.

    An issue names a known problem (optionally with a bug key such as
    `ref://JIRA/NET-1`) and owns rules; a rule stamps the results of one test
    that match its parameters, verdicts and run tags, under a category
    (`known-issue`, `flaky`, `to-investigate`, ...) and with a disposition
    (`expected` makes the stamped failures count as expected).

    Args:
        project_id: Optional project ID to filter by
        state: Semicolon-separated issue states: 'open', 'closed'
        category: Semicolon-separated categories; issues having an active
            rule of any of them
        rules: Semicolon-separated rules states: 'enforced' (an active rule
            of an open issue), 'dormant', 'deactivated', 'unruled'
        search: Text to look for in the title, description and bug key, or
            '#<id>' for an issue by ID
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)
        ordering: 'created_at', 'updated_at', 'title', 'state' or
            'result_count', with '-' in front for descending
            (default: '-created_at')
        created_after: Created on or after this date (yyyy-mm-dd)
        created_before: Created on or before this date (yyyy-mm-dd)
        updated_after: Changed on or after this date (yyyy-mm-dd)
        updated_before: Changed on or before this date (yyyy-mm-dd)
        test_name: Only the issues the results of this test are classified
            under (the name as get_history takes it); their ids are what
            the `issue` filter of get_history takes
        facets: true adds `facets`, the counts of issues by state,
            category, rules state and project. Each count tells how many
            issues the list would hold with that value of its filter and
            the other filters as given, so the counts of a filter ignore
            its own value (and the dates and test_name)

    Returns:
        Dictionary with pagination metadata and the issues, each with its
        rule, active rule and result counts and its active rules in brief
    """

    def _list():
        filters = {
            'project': project_id,
            'state': state,
            'category': category,
            'rules': rules,
            'search': search,
        }
        dates = {
            'created_after': created_after,
            'created_before': created_before,
            'updated_after': updated_after,
            'updated_before': updated_before,
        }
        queryset = IssueManagementService.list_issues(
            _query({**filters, **dates}),
            _ordering(ordering, IssueManagementService.ORDERING_FIELDS),
            test_name,
        )
        paged = PaginatedResult.paginate_queryset(queryset, page, page_size)
        listed = {
            'pagination': paged['pagination'],
            'issues': [_issue_data(issue) for issue in paged['results']],
        }
        if facets:
            listed['facets'] = asdict(IssueManagementService.facets(_query(filters)))
        return listed

    return await sync_to_async(_list)()


async def get_issue(issue_id: int) -> dict:
    """
    Get an issue with all of its rules.

    Args:
        issue_id: The ID of the issue

    Returns:
        Dictionary with `issue` (as list_issues shows it) and `rules`: every
        rule of the issue, active or not, with its test, matcher (parameters,
        verdicts, tags), category and disposition
    """

    def _get():
        issue = IssueManagementService.get(issue_id)
        rules = IssueRuleManagementService.of_issue(issue.id)
        return {'issue': _issue_data(issue), 'rules': _rules_data(rules)}

    return await sync_to_async(_get)()


async def get_run_issues(
    run_id: int,
    search: str | None = None,
    state: str | None = None,
    category: str | None = None,
    effect: str | None = None,
) -> list[dict]:
    """
    Summarize the issues the results of a run are classified under.

    Args:
        run_id: The ID of the test run
        search: Text to look for in the issue titles and bug keys
        state: Semicolon-separated issue states: 'open', 'closed'
        category: Semicolon-separated rule categories
        effect: Semicolon-separated effects of the rules on the run:
            'suppressed' (failures counted as expected), 'unexpected'
            (failures kept unexpected), 'marked' (no disposition), 'stale'
            (the issue is closed)

    Returns:
        One entry per issue: its id, title, state, bug key and URL, the
        number of results stamped under it in this run, and the rules that
        stamped them with their category, disposition and effect
    """

    def _summary():
        run = RunService.get_run(run_id)
        summaries = ClassificationService.filter_issues_summary(
            ClassificationService.run_issues_summary(run),
            search=search,
            state=state,
            category=category,
            effect=effect,
        )
        return [dict(row) for row in RunIssueSummarySerializer(summaries, many=True).data]

    return await sync_to_async(_summary)()


async def get_run_issue_results(
    run_id: int,
    issue_id: int,
    page: int | None = None,
    page_size: int | None = None,
    categories: str | None = None,
) -> dict:
    """
    List the results of a run classified under an issue, newest first.

    Args:
        run_id: The ID of the test run
        issue_id: The ID of the issue, as get_run_issues shows it
        categories: Semicolon-separated categories; keeps the results
            stamped by a rule of the issue in any of them
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)

    Returns:
        Dictionary with pagination metadata and the results as get_result_details
        shows them, with their issues and stamp ids
    """

    def _results():
        run = RunService.get_run(run_id)
        IssueManagementService.get(issue_id)
        return _paginated_result_details(
            ClassificationService.run_issue_results(run, issue_id, categories),
            page,
            page_size,
        )

    return await sync_to_async(_results)()


async def get_issue_results(  # noqa: PLR0913, PLR0917
    issue_id: int,
    results: str | None = None,
    result_properties: str | None = None,
    requirements: str | None = None,
    category: str | None = None,
    classification: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    verdicts: list[str] | None = None,
    artifacts: list[str] | None = None,
    parameters: list[str] | None = None,
    facets: bool = False,
) -> dict:
    """
    List every result classified under an issue, across runs, newest first.

    Args:
        issue_id: The ID of the issue
        results: Semicolon-separated obtained result statuses
            (e.g., 'PASSED;FAILED;SKIPPED;KILLED;CORED;FAKED;INCOMPLETE')
        result_properties: Semicolon-separated result properties
            (e.g., 'expected;unexpected;not_run')
        requirements: Semicolon-separated requirements the results carry
        category: Semicolon-separated categories of this issue's rules that
            stamped the results
        classification: Semicolon-separated classifications shown for the
            results: 'suppressed' (counted as expected), 'unexpected',
            'marked', 'stale' (the issue is closed), 'no-effect' (the result
            did not fail)
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 1000)
        verdicts: Verdicts the results must all carry
        artifacts: Artifacts the results must all carry
        parameters: Test parameters the results must all have, each as
            'name=value'
        facets: true adds `facets`, the values these filters can take, found
            over every result of the issue whatever the filters: results,
            result_properties, verdicts, categories, classifications,
            artifacts, parameters ('name=value') and requirements

    Returns:
        Dictionary with pagination metadata and the results as get_result_details
        shows them, each with the tags of its run
    """

    def _results():
        IssueManagementService.get(issue_id)
        given = {
            'results': results,
            'result_properties': result_properties,
            'requirements': requirements,
            'category': category,
            'classification': classification,
            'page': page,
            'page_size': page_size,
            'verdict': verdicts,
            'artifact': artifacts,
            'parameter': parameters,
        }
        query = IssueResultsQuerySerializer(
            data={name: value for name, value in given.items() if value is not None},
        )
        query.is_valid(raise_exception=True)
        params = query.validated_data
        listed = IssueResultsService.page(
            issue_id,
            IssueResultsFilters.from_params(params),
            page=params.get('page'),
            page_size=params.get('page_size'),
        )
        if facets:
            found = IssueResultsService.facets(issue_id)
            listed = {**listed, 'facets': dict(IssueResultsFacetsSerializer(found).data)}
        return listed

    return await sync_to_async(_results)()


def _rule_query(  # noqa: PLR0913, PLR0917
    project_id,
    issue_id,
    search,
    category,
    active,
    expected,
    issue_state,
    tags,
    verdicts,
    ignore_verdicts,
    parameters,
) -> MultiValueDict:
    # The verdicts filter takes whole lists as JSON, null for the rules ignoring them.
    verdict_lists = [json.dumps(verdicts)] if verdicts is not None else []
    if ignore_verdicts:
        verdict_lists.append('null')
    delimiter = settings.KEY_VALUE_DELIMITER
    return _query(
        {
            'project': project_id,
            'issue': issue_id,
            'search': search,
            'category': category,
            'active': None if active is None else str(active).lower(),
            'expected': expected,
            'issue_state': issue_state,
            'tag': tags,
            'verdicts': verdict_lists,
            'parameter': [
                f'{name}{delimiter}{value}' for name, value in (parameters or {}).items()
            ],
        },
    )


async def list_issue_rules(  # noqa: PLR0913, PLR0917
    project_id: int | None = None,
    issue_id: int | None = None,
    search: str | None = None,
    category: str | None = None,
    active: bool | None = None,
    expected: str | None = None,
    issue_state: str | None = None,
    tags: list[str] | None = None,
    verdicts: list[str] | None = None,
    ignore_verdicts: bool | None = None,
    parameters: dict | None = None,
    ordering: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
    facets: bool = False,
) -> dict:
    """
    List classification rules across issues, newest first.

    A rule belongs to an issue and stamps the results of one test that match
    its parameters, verdicts and run tags. Use get_issue for the rules of
    one issue together with the issue itself.

    Args:
        project_id: Optional project ID to filter by
        issue_id: Only the rules of this issue
        search: Text to look for in the test name, issue title and bug key
        category: Semicolon-separated categories ('product-defect',
            'test-bug', 'env', 'known-issue', 'flaky', 'to-investigate')
        active: true for the rules classifying later imports, false for
            the deactivated ones
        expected: Semicolon-separated dispositions: 'expected',
            'unexpected', 'none'
        issue_state: Semicolon-separated states of the rules' issues:
            'open', 'closed'
        tags: Run tags; rules containing any of them
        verdicts: A verdict list; rules matching exactly these verdicts, in
            order ([] for the rules of results without verdicts)
        ignore_verdicts: true for the rules that ignore the verdicts
        parameters: Test parameters as {name: value}; rules containing any
            of them
        ordering: 'created_at', 'category', 'active', 'test_name' or
            'issue_title', with '-' in front for descending
            (default: '-created_at')
        page: Page number (default: 1)
        page_size: Items per page (default: 25, max: 10000)
        facets: true adds `facets`, the counts of rules by each value the
            filters can take: `active`, `category`, `expected`,
            `issue_state`, `project` (by project ID), `tag`, `parameter`
            ('name=value') and `verdicts` (a JSON list, or null for the
            rules ignoring the verdicts). The counts of a filter ignore its
            own value and follow the other filters, so they also tell
            which tags, verdict lists and parameters the rules contain

    Returns:
        Dictionary with pagination metadata and the rules, as get_issue shows
        them: the issue, the test, the matcher (parameters, verdicts, tags),
        the category, the disposition and whether the rule is active
    """

    def _list():
        query = _rule_query(
            project_id,
            issue_id,
            search,
            category,
            active,
            expected,
            issue_state,
            tags,
            verdicts,
            ignore_verdicts,
            parameters,
        )
        queryset = IssueRuleManagementService.list_rules(
            query,
            _ordering(ordering, IssueRuleManagementService.ORDERING_FIELDS),
        )
        paged = PaginatedResult.paginate_queryset(queryset, page, page_size)
        listed = {
            'pagination': paged['pagination'],
            'rules': _rules_data(paged['results']),
        }
        if facets:
            listed['facets'] = asdict(IssueRuleManagementService.facets(query))
        return listed

    return await sync_to_async(_list)()


async def search_tests(project_id: int | None = None, search: str | None = None) -> list[dict]:
    """
    Find tests by path, for the `test_id` a rule needs.

    The same test name can occur under different packages, with a different
    ID each time, so the search matches the full path.

    Args:
        project_id: Only the tests that have results in this project
        search: Text to look for in the test paths; without it the tests
            most recently given a rule are returned

    Returns:
        Up to 20 tests, each with its id, name and path
    """

    def _search():
        options = IssueRuleManagementService.test_options(project_id, (search or '').strip())
        return [asdict(option) for option in options]

    return await sync_to_async(_search)()


async def list_issue_trackers(project_id: int | None = None) -> dict:
    """
    List the issue trackers a bug key may refer to.

    The bug key of an issue is written 'ref://<TRACKER>/<KEY>', such as
    'ref://JIRA/NET-1', where TRACKER is one of the tracker ids listed here.

    Args:
        project_id: The project whose references config to read; the global
            one without it

    Returns:
        Dictionary with `trackers`: by tracker id, its name and the URI its
        bug URLs start with
    """

    def _trackers():
        return {'trackers': ConfigServices.issue_trackers(project_id) or {}}

    return await sync_to_async(_trackers)()


async def get_classify_options(result_ids: list[int]) -> dict:
    """
    Tell what the rules classifying the given results may contain.

    The results must be test results of one project; they may be of several
    runs, as picked from a test's history. The results of each test get a
    rule per group of runs with the same important tags (joined by the runs
    whose important tags contain those of the group), and each rule must
    match every result it classifies, so for each rule this lists the
    parameters all of its results share, the different verdict lists they
    have, and the tags a rule may use.

    Args:
        result_ids: Result IDs, as shown by get_run_leaf_results or get_history

    Returns:
        Dictionary with `rules`, one per test and group of runs: test id,
        name and path, run ids (with the test, what a matcher names the rule
        by), result ids, shared parameters, verdict lists and tags
        (`default`: the group's important tags, `allowed`: every tag the
        rule may contain)
    """

    def _options():
        # Checked as the REST endpoint checks them: an empty list has no run
        # to take the tags from.
        body = ClassifyOptionsRequestSerializer(data={'result_ids': result_ids})
        body.is_valid(raise_exception=True)
        ids = body.validated_data['result_ids']
        found = ResultService.get_results_by_ids(ids)
        options = ResultService.get_classify_options(
            ResultService.validate_results_for_classification(ids, found),
        )
        return {
            'rules': [
                dict(row) for row in ClassifyOptionsRuleSerializer(options, many=True).data
            ]
        }

    return await sync_to_async(_options)()


# Classification write tools. Each needs a token and the `manage_issues`
# permission in the projects of what it changes, as the REST endpoints do.


def _disposition(expected: str | None) -> dict:
    """
    The `expected` field of a request for the disposition a caller named.

    Empty when the caller named none, so that the category decides for a new
    rule and an edited rule keeps its own. A rule may also have no
    disposition, which 'none' asks for and an omitted argument cannot.
    """
    if expected is None:
        return {}
    if expected not in DISPOSITION_TO_EXPECTED:
        msg = f'Expected any of: {", ".join(DISPOSITION_TO_EXPECTED)}.'
        raise ValidationError({'expected': msg})
    return {'expected': DISPOSITION_TO_EXPECTED[expected]}


@mcp_auth_required()
async def classify_results(
    result_ids: list[int],
    categories: list[str],
    matchers: list[dict],
    issue_id: int | None = None,
    new_issue: dict | None = None,
    scope: str = 'future',
    expected: str | None = None,
) -> dict:
    """
    Classify results of one project under an issue, acting as the caller.

    Pass either `issue_id` (an existing issue of the results' project) or
    `new_issue` ({title, description?, bug_key?}) to create one. Each rule
    get_classify_options gives (a test and a group of its runs) becomes a
    rule per category, built from its matcher: {test_id, run_ids,
    parameters, verdicts, tags}, with `test_id` and `run_ids` as the rule
    has them. Take the rest from the rule's options: `parameters` any part
    of the shared ones ({} for none), `verdicts` null to ignore them or the
    options' `verdicts` whole: a list of verdict lists such as
    [['Timed out']], each of which makes a rule, `tags` any of the allowed
    ones ([] for none). `scope='future'` makes rules that also classify
    later imports; 'oneoff' only stamps these results. Requires an access
    token and the `manage_issues` permission.

    Args:
        result_ids: The results, all of one project, as shown by
            get_run_leaf_results or get_history
        categories: 'known-issue', 'flaky', 'to-investigate', ... (a rule for each)
        matchers: One matcher per rule of get_classify_options
        issue_id: The issue to classify under
        new_issue: The fields of an issue to create instead: title (required),
            description, bug_key (e.g. 'ref://JIRA/NET-1')
        scope: 'future' (default) or 'oneoff'
        expected: 'expected' (the failures count as expected), 'unexpected'
            or 'none' (the results are only marked); omit for the category's
            own disposition (known-issue, env, test-bug and flaky: expected;
            product-defect and to-investigate: unexpected)

    Returns:
        The issue id and the ids of the rules the results are stamped with
    """

    def _classify():
        if (issue_id is None) == (new_issue is None):
            msg = 'Pass exactly one of issue_id and new_issue.'
            raise ValidationError(msg)
        body = ClassifyRequestSerializer(
            data={
                'result_ids': result_ids,
                'issue': issue_id if issue_id is not None else new_issue,
                'categories': categories,
                'matchers': matchers,
                'scope': scope,
                **_disposition(expected),
            },
        )
        body.is_valid(raise_exception=True)
        fields = body.validated_data

        found = ResultService.get_results_by_ids(fields['result_ids'])
        # Before anything is told about the results, as it depends on their projects.
        actor = authorize_action(MANAGE_ISSUES, [r.project_id for r in found.values()])
        results = ResultService.validate_results_for_classification(fields['result_ids'], found)
        outcome = ResultService.classify(
            results,
            issue=fields['issue'],
            categories=fields['categories'],
            matchers=fields['matchers'],
            scope=fields['scope'],
            expected=fields.get('expected', UNSET),
            actor=actor,
        )
        return asdict(outcome)

    return await sync_to_async(_classify)()


@mcp_auth_required()
async def unclassify_results(rule_result_ids: list[int]) -> dict:
    """
    Take a manual classification off results, acting as the caller.

    Deletes the given stamps (the `rule_result_id` of a result's issues, as
    get_run_leaf_results and get_result_details show them), then the rules
    left without stamps and the issues left without rules. Stamps made by
    import cannot be taken off: deactivate or delete the rule instead.
    Requires an access token and the `manage_issues` permission.

    Args:
        rule_result_ids: The stamps to take off

    Returns:
        The ids of the stamps, rules and issues deleted
    """

    def _unclassify():
        ids = list(dict.fromkeys(rule_result_ids))
        found = ResultService.get_rule_results_by_ids(ids)
        # Before anything is told about the stamps, as it depends on their projects.
        authorize_action(MANAGE_ISSUES, [rr.result.project_id for rr in found.values()])
        return asdict(ResultService.unclassify_results(ids, found))

    return await sync_to_async(_unclassify)()


@mcp_auth_required()
async def apply_run_rules(run_id: int) -> dict:
    """
    Apply the project's active rules to a run, acting as the caller.

    Adds the stamps that are missing on the run's results; nothing is
    removed, so it is safe to repeat. Requires an access token and the
    `manage_issues` permission.

    Args:
        run_id: The ID of the test run

    Returns:
        The number of stamps created
    """

    def _apply():
        run = RunService.get_run(run_id)
        actor = authorize_action(MANAGE_ISSUES, [run.project_id])
        created = ClassificationService.apply_active_rules_manual(run, actor=actor)
        return {'stamps_created': created}

    return await sync_to_async(_apply)()


@mcp_auth_required()
async def edit_issue(
    issue_id: int | None = None,
    project_id: int | None = None,
    title: str | None = None,
    description: str | None = None,
    bug_key: str | None = None,
) -> dict:
    """
    Create an issue, or change one, acting as the caller.

    Without `issue_id` a new issue is created in `project_id` with `title`
    (required). With it, the fields passed replace the issue's: pass an empty
    string to clear the description or the bug key. The state is changed by
    set_issues_state. Requires an access token and the
    `manage_issues` permission.

    Args:
        issue_id: The issue to change; omit it to create one
        project_id: The project of a new issue (or to move an issue without stamps to)
        title: The title
        description: Internal notes
        bug_key: The bug, as 'ref://TRACKER/KEY'; it links to the bug when
            TRACKER is one list_issue_trackers shows for the project. It
            cannot change once the issue has classified results

    Returns:
        The issue as list_issues shows it
    """

    def _edit():
        given = {
            'project': project_id,
            'title': title,
            'description': description,
            'bug_key': bug_key,
        }
        fields = {name: value for name, value in given.items() if value is not None}
        if issue_id is None:
            serializer = IssueManagementService.validate(fields)
            actor = authorize_action(MANAGE_ISSUES, [serializer.validated_data['project'].id])
            issue = IssueManagementService.create(serializer, actor)
        else:
            issue = IssueManagementService.get(issue_id)
            serializer = IssueManagementService.validate(fields, issue)
            # The issue may be moved to another project, both must allow it.
            new_project = serializer.validated_data.get('project')
            actor = authorize_action(
                MANAGE_ISSUES,
                [issue.project_id, new_project.id if new_project else issue.project_id],
            )
            issue = IssueManagementService.update(serializer, actor)
        return _issue_data(IssueManagementService.get(issue.id))

    return await sync_to_async(_edit)()


@mcp_auth_required()
async def delete_issue(issue_id: int) -> dict:
    """
    Delete an issue with its rules and stamps for good, acting as the caller.

    Prefer closing it with set_issues_state: a closed issue keeps its
    history and stops classifying. Requires an access token and the `manage_issues` permission.

    Args:
        issue_id: The issue to delete

    Returns:
        `{'deleted': issue_id}`
    """

    def _delete():
        issue = IssueManagementService.get(issue_id)
        authorize_action(MANAGE_ISSUES, [issue.project_id])
        IssueManagementService.delete(issue)
        return {'deleted': issue_id}

    return await sync_to_async(_delete)()


@mcp_auth_required()
async def set_issues_state(issue_ids: list[int], state: str) -> dict:
    """
    Close issues or reopen them, acting as the caller.

    Closing deactivates the issue's rules, so they classify nothing new; the
    stamps stay and are shown as stale. Reopening leaves the rules
    deactivated: activate the ones wanted with set_rules_active. Requires an
    access token and the `manage_issues` permission.

    Args:
        issue_ids: The issues to close or reopen
        state: 'closed' or 'open'

    Returns:
        The ids split into updated, unchanged (already in that state) and
        not_found
    """

    def _set():
        changes = {
            'closed': IssueManagementService.close,
            'open': IssueManagementService.reopen,
        }
        if state not in changes:
            msg = "The state is 'open' or 'closed'."
            raise ValidationError(msg)
        ids = list(dict.fromkeys(issue_ids))
        actor = authorize_action(MANAGE_ISSUES, IssueManagementService.projects_of(ids))
        return asdict(changes[state](ids, actor))

    return await sync_to_async(_set)()


@mcp_auth_required()
async def edit_issue_rule(  # noqa: PLR0913, PLR0917
    rule_id: int | None = None,
    issue_id: int | None = None,
    test_id: int | None = None,
    category: str | None = None,
    parameters: dict | None = None,
    verdicts: list[str] | None = None,
    ignore_verdicts: bool | None = None,
    tags: list[str] | None = None,
    expected: str | None = None,
) -> dict:
    """
    Create a rule of an issue, or change one, acting as the caller.

    A rule stamps the results of `test_id` whose parameters include
    `parameters`, whose verdicts are exactly `verdicts` (in order; [] for
    none) unless `ignore_verdicts` is true, in runs carrying every one of
    `tags`. Without `rule_id` all of issue_id, test_id, category, parameters,
    tags and either verdicts or ignore_verdicts are required. With it, the
    fields passed replace the rule's; the matcher of a rule that has
    classified results cannot change, create a new rule instead. Requires
    an access token and the `manage_issues` permission.

    Args:
        rule_id: The rule to change; omit it to create one
        issue_id: The issue the rule belongs to
        test_id: The test, as shown by get_run_overview
        category: 'known-issue', 'flaky', 'to-investigate', ...
        parameters: Test parameters as {name: value}; {} restricts nothing
        verdicts: The verdicts a result must have, in order; [] for none
        ignore_verdicts: true to match whatever the verdicts are
        tags: Run tags as 'name=value' or 'name'; [] restricts nothing
        expected: 'expected' (the stamped failures count as expected),
            'unexpected' or 'none' (the results are only marked); omit for
            the category's own disposition when creating

    Returns:
        The rule as get_issue shows it
    """

    def _edit():
        given = {
            'issue': issue_id,
            'test': test_id,
            'category': category,
            'parameters': parameters,
            'verdicts': verdicts,
            'tags': tags,
        }
        fields = {name: value for name, value in given.items() if value is not None}
        fields.update(_disposition(expected))
        if ignore_verdicts:
            fields['verdicts'] = None
        if rule_id is None:
            serializer = IssueRuleManagementService.validate(fields)
            actor = authorize_action(
                MANAGE_ISSUES,
                [serializer.validated_data['issue'].project_id],
            )
            rule = IssueRuleManagementService.create(serializer, actor)
        else:
            rule = IssueRuleManagementService.get(rule_id)
            serializer = IssueRuleManagementService.validate(fields, rule)
            # The rule may be moved to an issue of another project, both must allow it.
            new_issue = serializer.validated_data.get('issue')
            actor = authorize_action(
                MANAGE_ISSUES,
                [rule.issue.project_id, (new_issue or rule.issue).project_id],
            )
            rule = IssueRuleManagementService.update(serializer, actor)
        return _rule_data(IssueRuleManagementService.get(rule.id))

    return await sync_to_async(_edit)()


@mcp_auth_required()
async def delete_issue_rule(rule_id: int) -> dict:
    """
    Delete a rule with its stamps for good, acting as the caller.

    Prefer deactivating it with set_rules_active, which keeps the stamps.
    Requires an access token and the `manage_issues` permission.

    Args:
        rule_id: The rule to delete

    Returns:
        `{'deleted': rule_id}`
    """

    def _delete():
        rule = IssueRuleManagementService.get(rule_id)
        authorize_action(MANAGE_ISSUES, [rule.issue.project_id])
        IssueRuleManagementService.delete(rule)
        return {'deleted': rule_id}

    return await sync_to_async(_delete)()


@mcp_auth_required()
async def set_rules_active(rule_ids: list[int], active: bool) -> dict:
    """
    Activate rules or deactivate them, acting as the caller.

    An active rule classifies later imports; a deactivated one keeps its
    stamps. The rules of a closed issue cannot be activated: reopen it
    first. Requires an access token and the `manage_issues` permission.

    Args:
        rule_ids: The rules to change
        active: true to activate them, false to deactivate them

    Returns:
        The ids split into updated, unchanged (already so) and not_found
    """

    def _set():
        ids = list(dict.fromkeys(rule_ids))
        actor = authorize_action(MANAGE_ISSUES, IssueRuleManagementService.projects_of(ids))
        change = (
            IssueRuleManagementService.activate
            if active
            else IssueRuleManagementService.deactivate
        )
        return asdict(change(ids, actor))

    return await sync_to_async(_set)()


# Shared registry of Bublik tool callables. Consumed both by the FastMCP HTTP
# server (see register_tools) and directly by the in-process chat agent
# (see bublik.ai.agent), so both expose exactly the same tools.
MCP_TOOLS = [
    get_run_overview,
    get_run_leaf_results,
    get_result_details,
    list_projects,
    list_runs,
    get_latest_run_date,
    get_dashboard,
    get_log_urls,
    get_log_overview,
    get_log_lines,
    get_tree_path,
    get_history,
    get_run_requirements,
    get_run_comment,
    get_run_report_configs,
    get_server_version,
    search_docs,
    get_doc,
    list_docs,
    get_test_comments,
    list_issues,
    get_issue,
    get_run_issues,
    get_run_issue_results,
    get_issue_results,
    list_issue_rules,
    search_tests,
    list_issue_trackers,
    get_classify_options,
]


# Tools that write; each requires a caller.
MCP_WRITE_TOOLS = [
    edit_test_comment,
    delete_test_comment,
    edit_run_comment,
    delete_run_comment,
    set_run_compromised,
    classify_results,
    unclassify_results,
    apply_run_rules,
    edit_issue,
    delete_issue,
    set_issues_state,
    edit_issue_rule,
    delete_issue_rule,
    set_rules_active,
]

# The action a write tool needs permitted, in the projects it changes.
MCP_TOOL_ACTIONS = {
    edit_test_comment: MANAGE_TEST_COMMENTS,
    delete_test_comment: MANAGE_TEST_COMMENTS,
    classify_results: MANAGE_ISSUES,
    unclassify_results: MANAGE_ISSUES,
    apply_run_rules: MANAGE_ISSUES,
    edit_issue: MANAGE_ISSUES,
    delete_issue: MANAGE_ISSUES,
    set_issues_state: MANAGE_ISSUES,
    edit_issue_rule: MANAGE_ISSUES,
    delete_issue_rule: MANAGE_ISSUES,
    set_rules_active: MANAGE_ISSUES,
}
MCP_ACTIONS = frozenset(MCP_TOOL_ACTIONS.values())

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
    get_run_leaf_results,
    get_result_details,
    list_projects,
    list_runs,
    get_latest_run_date,
    get_dashboard,
    get_history,
    get_run_comment,
    get_run_report_configs,
    get_test_comments,
    list_issues,
    get_issue,
    get_run_issues,
    get_run_issue_results,
    get_issue_results,
    list_issue_rules,
    search_tests,
    list_issue_trackers,
    get_classify_options,
]

MCP_WRITE_TOOL_NAMES = [tool.__name__ for tool in MCP_WRITE_TOOLS]
MCP_ADMIN_TOOL_NAMES = [tool.__name__ for tool in MCP_ADMIN_TOOLS]
MCP_UNCACHED_TOOL_NAMES = [tool.__name__ for tool in MCP_UNCACHED_TOOLS]


def tools_within(admin: bool, actions) -> list:
    """The tools of a logged-in caller who may perform ``actions`` somewhere."""
    writes = [
        tool
        for tool in MCP_WRITE_TOOLS
        if tool not in MCP_TOOL_ACTIONS or MCP_TOOL_ACTIONS[tool] in actions
    ]
    return [*MCP_TOOLS, *writes, *(MCP_ADMIN_TOOLS if admin else [])]


def tools_for(user) -> list:
    """The tools ``user`` may call in at least one project; the reads for nobody."""
    if user is None:
        return list(MCP_TOOLS)
    return tools_within(is_admin(user), actions_permitted_somewhere(MCP_ACTIONS, user))


def refusal_text(detail: object) -> str:
    """
    The detail of a REST framework error as one line of text.

    The detail is an ErrorDetail, a list of them or a dict of them by field,
    and its own str() is the repr: [ErrorDetail(string='...', code='...')].
    """
    if isinstance(detail, (list, tuple)):
        return '; '.join(refusal_text(item) for item in detail)
    if isinstance(detail, dict):
        return '; '.join(f'{key}: {refusal_text(value)}' for key, value in detail.items())
    return str(detail)


def _refusing_in_plain_text(tool):
    """
    The tool, with the errors of the services worded for the caller.

    FastMCP reports a ToolError as it is, and anything else as a failure of
    the tool: logged with its traceback and shown as its str().
    """

    @wraps(tool)
    async def wrapper(*args, **kwargs):
        try:
            return await tool(*args, **kwargs)
        except APIException as error:
            raise ToolError(refusal_text(error.detail)) from error

    return wrapper


def register_tools(mcp: FastMCP) -> None:
    """
    Register all shared Bublik tools with the FastMCP server.
    """
    for tool in MCP_TOOLS:
        mcp.tool(_refusing_in_plain_text(tool))
    for tool in MCP_WRITE_TOOLS:
        mcp.tool(_refusing_in_plain_text(tool), tags={'write'})
    for tool in MCP_ADMIN_TOOLS:
        mcp.tool(_refusing_in_plain_text(tool), tags={'write', 'admin'})
