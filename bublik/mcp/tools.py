# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import date, timedelta
import logging
from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async

from bublik.core.dashboard import DashboardService
from bublik.core.history.services import HistoryService
from bublik.core.log.services import LogService
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.project import ProjectService
from bublik.core.result import ResultService
from bublik.core.run.services import RunService
from bublik.core.run.stats import generate_runs_details, get_test_runs
from bublik.core.server.services import ServerService
from bublik.core.tree.services import TreeService
from bublik.mcp.models import JsonLog
from bublik.mcp.processor import LogProcessor


if TYPE_CHECKING:
    from fastmcp import FastMCP


logger = logging.getLogger(__name__)


def get_default_date_range():
    '''
    Calculate default date range: 6 months ago to today.

    Returns:
        Tuple of (from_date, to_date) as ISO format strings (yyyy-mm-dd)
    '''
    to_date = date.today()

    from_date = to_date - timedelta(days=180)
    return from_date.isoformat(), to_date.isoformat()


def register_tools(mcp: FastMCP):  # noqa: C901
    '''
    Register all MCP tools with the FastMCP server.
    '''

    @mcp.tool()
    async def get_run_details(run_id: int) -> dict:
        '''
        Get detailed information about a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with full run details including metadata, stats, etc.
        '''
        return await sync_to_async(RunService.get_run_details)(run_id)

    @mcp.tool()
    async def get_run_status(run_id: int) -> str:
        '''
        Get the status of a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Status string for the run (e.g., 'passed', 'failed', 'skipped')
        '''
        return await sync_to_async(RunService.get_run_status)(run_id)

    @mcp.tool()
    async def get_run_stats(
        run_id: int,
        requirements: str | None = None,
    ) -> dict:
        '''
        Get statistics for a test run.

        Args:
            run_id: The ID of the test run
            requirements: Optional requirements filter

        Returns:
            Dictionary with run statistics including pass/fail counts
        '''
        return await sync_to_async(RunService.get_run_stats)(run_id, requirements)

    @mcp.tool()
    async def get_run_source(run_id: int) -> str:
        '''
        Get the source URL for a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Source URL string
        '''
        return await sync_to_async(RunService.get_run_source)(run_id)

    @mcp.tool()
    async def get_run_compromised(run_id: int) -> dict:
        '''
        Get the compromised status of a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with compromised status data including comment and bug ID
        '''
        return await sync_to_async(RunService.get_run_compromised)(run_id)

    @mcp.tool()
    async def get_result_details(result_id: int) -> dict:
        '''
        Get detailed information about a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with full result details
        '''
        return await sync_to_async(ResultService.get_result_details)(result_id)

    @mcp.tool()
    async def get_result_artifacts_and_verdicts(result_id: int) -> dict:
        '''
        Get artifacts and verdicts for a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with artifacts and verdicts lists
        '''
        return await sync_to_async(ResultService.get_result_artifacts_and_verdicts)(result_id)

    @mcp.tool()
    async def list_results(
        parent_id: int,
        test_name: str,
        results: str | None = None,
        result_properties: str | None = None,
        requirements: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        '''
        List test results with filtering.

        Args:
            parent_id: Filter by parent package ID
            test_name: Filter by test name
            results: Semicolon-separated result statuses
                (e.g., 'PASSED;FAILED;SKIPPED;KILLED;CORED;FAKED;INCOMPLETE')
            result_properties: Semicolon-separated result properties
                (e.g., 'expected;unexpected;not_run')
            requirements: Semicolon-separated requirement names
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and result details
        '''
        return await sync_to_async(ResultService.list_results_paginated)(
            parent_id=parent_id,
            test_name=test_name,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
            page=page,
            page_size=page_size,
        )

    # Project tools

    @mcp.tool()
    async def list_projects() -> list[dict]:
        '''
        List all available projects.

        Returns:
            List of projects with id and name
        '''
        return await sync_to_async(ProjectService.list_projects)()

    @mcp.tool()
    async def get_project(project_id: int) -> dict:
        '''
        Get details of a specific project.

        Args:
            project_id: The ID of the project

        Returns:
            Dictionary with project id and name
        '''
        return await sync_to_async(ProjectService.get_project)(project_id)

    # Runs tools

    @mcp.tool()
    async def list_runs(  # noqa: PLR0913
        start_date: str | None = None,
        finish_date: str | None = None,
        project_id: int | None = None,
        run_status: str | None = None,
        run_data: str | None = None,
        tag_expr: str | None = None,
        label_expr: str | None = None,
        revision_expr: str | None = None,
        branch_expr: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        '''
        List test runs with comprehensive filtering.

        Args:
            start_date: Start date in yyyy-mm-dd format (e.g., '2024-01-01')
            finish_date: Finish date in yyyy-mm-dd format (e.g., '2024-01-31')
            project_id: Optional project ID to filter results
            run_status: Optional run status filter
            run_data: Semicolon-separated metadata (tags, labels, revisions, branches)
            tag_expr: Tag expression filter (supports boolean logic: &, |, !)
            label_expr: Label expression filter (supports boolean logic: &, |, !)
            revision_expr: Revision expression filter (supports boolean logic: &, |, !)
            branch_expr: Branch expression filter (supports boolean logic: &, |, !)
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and run details
        '''
        queryset = await sync_to_async(RunService.list_runs_queryset)(
            start_date=start_date,
            finish_date=finish_date,
            project_id=project_id,
            run_status=run_status,
            run_data=run_data,
            tag_expr=tag_expr,
            label_expr=label_expr,
            revision_expr=revision_expr,
            branch_expr=branch_expr,
        )
        runs_details = await sync_to_async(generate_runs_details)(queryset)
        return await sync_to_async(PaginatedResult.paginate_queryset)(
            runs_details,
            page,
            page_size,
        )

    @mcp.tool()
    async def list_runs_today(
        project_id: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        '''
        List test runs for today.

        Args:
            project_id: Optional project ID to filter results
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and today's run details
        '''
        today = date.today().isoformat()
        queryset = await sync_to_async(RunService.list_runs_queryset)(
            start_date=today,
            finish_date=today,
            project_id=project_id,
        )
        runs_details = await sync_to_async(generate_runs_details)(queryset)
        return await sync_to_async(PaginatedResult.paginate_queryset)(
            runs_details,
            page,
            page_size,
        )

    @mcp.tool()
    async def get_latest_run_date(project_id: int | None = None) -> str | None:
        '''
        Get the most recent run date.

        Args:
            project_id: Optional project ID to filter by

        Returns:
            Date string in yyyy-mm-dd format, or None if no runs exist
        '''

        def _get_latest_date():
            runs = get_test_runs(order_by='-start')
            if project_id:
                runs = runs.filter(project_id=project_id)
            latest = runs.first()
            return latest.start.date().isoformat() if latest else None

        return await sync_to_async(_get_latest_date)()

    # Dashboard tools

    @mcp.tool()
    async def get_dashboard(
        date: str,
        project_id: int | None = None,
        sort_by: str | None = None,
        validate: bool = False,
    ) -> dict:
        '''
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
        '''
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

    @mcp.tool()
    async def get_dashboard_today(
        project_id: int | None = None,
        sort_by: str | None = None,
    ) -> dict:
        '''
        Get dashboard data for today (structured format).

        If no data exists for today, returns the most recent dashboard data available.
        This matches the behavior of the REST API /api/v2/dashboard/ endpoint.

        Args:
            project_id: Optional project ID to filter results
            sort_by: Optional comma-separated column keys to sort by (e.g., 'start,total')

        Returns:
            Dictionary with the latest dashboard structure
        '''
        # Get latest available date (matches REST API behavior)
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

    @mcp.tool()
    async def get_latest_dashboard_date(project_id: int | None = None) -> str | None:
        '''
        Get the most recent date with dashboard data.

        Args:
            project_id: Optional project ID to filter by

        Returns:
            Date string in yyyy-mm-dd format, or None if no data
        '''
        return await sync_to_async(DashboardService.get_latest_dashboard_date)(
            project_id=project_id,
        )

    # Log tools

    @mcp.tool()
    async def get_log_urls(result_id: int, page: int | None = None) -> dict:
        '''
        Get log URLs for a test result (without fetching content).

        Args:
            result_id: The ID of the test result
            page: Optional page number (0 for all pages combined, >0 for specific page)

        Returns:
            Dictionary with 'url' and 'attachments_url' keys
        '''
        return await sync_to_async(LogService.get_json_log_urls)(
            result_id,
            page,
            request_origin=None,
        )

    @mcp.tool()
    async def get_log_html_url(result_id: int) -> str | None:
        '''
        Get HTML log URL for a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            URL string or None if not available
        '''
        return await sync_to_async(LogService.get_html_log_url)(result_id)

    LOG_RETURN_FORMAT: str = 'markdown'  # noqa: N806

    @mcp.tool()
    async def get_log_overview(
        result_id: int,
        page: int | None = None,
        include_scenario: bool = True,
        max_content_length: int | None = 200,
    ) -> dict | str:
        '''
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
        '''

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

    @mcp.tool()
    async def get_log_lines(  # noqa: PLR0913
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
        '''
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
        '''

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

    @mcp.tool()
    async def get_log_line(
        result_id: int,
        line_number: int,
        page: int | None = None,
    ) -> dict | str:
        '''
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
        '''

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

    @mcp.tool()
    async def get_tree_path(result_id: int) -> list[int]:
        '''
        Get path to a specific test result in the tree.

        Args:
            result_id: The ID of the test result

        Returns:
            List of node IDs from root to the specified result
        '''
        return await sync_to_async(TreeService.get_tree_path)(result_id)

    # History tools

    @mcp.tool()
    async def get_history(  # noqa: PLR0913
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
        '''
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
        '''
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

    @mcp.tool()
    async def get_history_grouped(  # noqa: PLR0913
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
        '''
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
        '''
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

    @mcp.tool()
    async def get_run_requirements(run_id: int) -> list[str]:
        '''
        Get requirements for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Sorted list of requirement strings
        '''
        return await sync_to_async(RunService.get_run_requirements)(run_id)

    @mcp.tool()
    async def get_run_comment(run_id: int) -> str | None:
        '''
        Get comment for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Comment string or None if no comment exists
        '''
        return await sync_to_async(RunService.get_run_comment)(run_id)

    # Server tools

    @mcp.tool()
    async def get_server_version() -> dict:
        '''
        Get server version information.

        Returns:
            Dictionary with repository revision information
        '''
        return await sync_to_async(ServerService.get_version)()
