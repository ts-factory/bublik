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
from bublik.core.measurement.services import MeasurementService
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.project import ProjectService
from bublik.core.report.services import ReportService
from bublik.core.result import ResultService
from bublik.core.run.services import RunService
from bublik.core.run.stats import generate_runs_details, get_test_runs
from bublik.core.server.services import ServerService
from bublik.core.tree.services import TreeService
from bublik.data.models import Measurement
from bublik.data.serializers import MeasurementSerializer


if TYPE_CHECKING:
    from fastmcp import FastMCP


logger = logging.getLogger(__name__)


def get_default_date_range():
    '''Calculate default date range: 6 months ago to today.

    Returns:
        Tuple of (from_date, to_date) as ISO format strings (yyyy-mm-dd)
    '''
    to_date = date.today()

    from_date = to_date - timedelta(days=180)
    return from_date.isoformat(), to_date.isoformat()


def register_tools(mcp: FastMCP):  # noqa: C901
    '''Register all MCP tools with the FastMCP server.'''

    @mcp.tool()
    def get_server_info() -> str:
        '''Get basic MCP server information.

        Returns server name, version, and status.
        '''
        tools_list = [
            'get_server_info',
            'get_run_details',
            'get_run_status',
            'get_run_stats',
            'get_run_source',
            'get_run_compromised',
            'get_result_details',
            'get_result_measurements',
            'get_result_artifacts_and_verdicts',
            'list_measurements',
            'get_measurement',
            'get_measurement_trend_charts',
            'get_measurements_by_result_ids',
            'list_projects',
            'get_project',
            'list_runs',
            'list_runs_today',
            'get_latest_run_date',
            'get_dashboard',
            'get_dashboard_today',
            'get_latest_dashboard_date',
            'get_log_json',
            'get_log_urls',
            'get_log_html_url',
            'get_tree',
            'get_tree_path',
            'get_history',
            'get_history_grouped',
            'get_run_requirements',
            'get_run_nok_distribution',
            'get_run_comment',
            'list_results',
            'get_server_version',
            'get_report_configs',
            'get_report',
        ]

        prompts_list = [
            'daily_test_health_check',
            'investigate_test_failure',
            'performance_trend_analysis',
            'compare_test_runs',
            'root_cause_analysis',
            'test_report_generator',
        ]

        summary_mcp_server: str = f'''Bublik MCP Server
        - Tools: {", ".join(tools_list)}
        - Prompts: {", ".join(prompts_list)}
        '''

        return summary_mcp_server

    @mcp.tool()
    async def get_run_details(run_id: int) -> dict:
        '''Get detailed information about a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with full run details including metadata, stats, etc.
        '''
        result = await sync_to_async(RunService.get_run_details)(run_id)
        for key in ['branches', 'labels']:
            if key in result and hasattr(result[key], '__iter__'):
                result[key] = list(result[key])
        return result

    @mcp.tool()
    async def get_run_status(run_id: int) -> str:
        """Get the status of a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Status string for the run (e.g., 'passed', 'failed', 'skipped')
        """
        return await sync_to_async(RunService.get_run_status)(run_id)

    @mcp.tool()
    async def get_run_stats(
        run_id: int,
        requirements: str | None = None,
    ) -> dict:
        '''Get statistics for a test run.

        Args:
            run_id: The ID of the test run
            requirements: Optional requirements filter

        Returns:
            Dictionary with run statistics including pass/fail counts
        '''
        return await sync_to_async(RunService.get_run_stats)(run_id, requirements)

    @mcp.tool()
    async def get_run_source(run_id: int) -> str:
        '''Get the source URL for a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Source URL string
        '''
        return await sync_to_async(RunService.get_run_source)(run_id)

    @mcp.tool()
    async def get_run_compromised(run_id: int) -> dict:
        '''Get the compromised status of a test run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with compromised status data including comment and bug ID
        '''
        return await sync_to_async(RunService.get_run_compromised)(run_id)

    @mcp.tool()
    async def get_result_details(result_id: int) -> dict:
        '''Get detailed information about a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with full result details
        '''
        return await sync_to_async(ResultService.get_result_details)(result_id)

    @mcp.tool()
    async def get_result_measurements(result_id: int) -> dict:
        '''Get measurements for a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with run_id, iteration_id, charts, and tables
        '''
        return await sync_to_async(ResultService.get_result_measurements)(result_id)

    @mcp.tool()
    async def get_result_artifacts_and_verdicts(result_id: int) -> dict:
        '''Get artifacts and verdicts for a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with artifacts and verdicts lists
        '''
        return await sync_to_async(ResultService.get_result_artifacts_and_verdicts)(result_id)

    @mcp.tool()
    async def get_measurement(measurement_id: int) -> dict:
        '''Get details of a specific measurement.

        Args:
            measurement_id: The ID of the measurement

        Returns:
            Dictionary with full measurement details

        Raises:
            ValidationError: if measurement not found
        '''

        def _get_measurement():
            measurement = MeasurementService.get_measurement(measurement_id)
            measurement = Measurement.objects.prefetch_related('metas').get(id=measurement.id)
            serializer = MeasurementSerializer(measurement)
            return serializer.data

        return await sync_to_async(_get_measurement)()

    @mcp.tool()
    async def get_measurement_trend_charts(result_ids: list[int]) -> list:
        '''Get measurement trend charts grouped by measurement for multiple result IDs.

        This provides trend analysis across multiple test results, with measurements
        grouped by their measurement_group_key.

        Args:
            result_ids: List of test result IDs to analyze

        Returns:
            List of chart representations

        Raises:
            ValidationError: if result_ids is empty
        '''
        return await sync_to_async(MeasurementService.get_trend_charts)(result_ids)

    @mcp.tool()
    async def get_measurements_by_result_ids(result_ids: list[int]) -> list[dict]:
        '''Get measurements with parameters for each result ID.

        For each result, includes:
        - run_id, result_id, start time, test_name
        - parameters_list (from test arguments)
        - measurement_series_charts

        Args:
            result_ids: List of test result IDs

        Returns:
            List of dictionaries containing measurement data for each result

        Raises:
            ValidationError: if result_ids is empty
        '''
        return await sync_to_async(MeasurementService.get_measurements_by_result_ids)(
            result_ids,
        )

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
        """List test results with filtering.

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
        """
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
        '''List all available projects.

        Returns:
            List of projects with id and name
        '''
        return await sync_to_async(ProjectService.list_projects)()

    @mcp.tool()
    async def get_project(project_id: int) -> dict:
        '''Get details of a specific project.

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
        """List test runs with comprehensive filtering.

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
        """
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
        """List test runs for today.

        Args:
            project_id: Optional project ID to filter results
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and today's run details
        """
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
        '''Get the most recent run date.

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
        """Get dashboard data for a specific date (structured format).

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

    @mcp.tool()
    async def get_dashboard_today(
        project_id: int | None = None,
        sort_by: str | None = None,
    ) -> dict:
        """Get dashboard data for today (structured format).

        If no data exists for today, returns the most recent dashboard data available.
        This matches the behavior of the REST API /api/v2/dashboard/ endpoint.

        Args:
            project_id: Optional project ID to filter results
            sort_by: Optional comma-separated column keys to sort by (e.g., 'start,total')

        Returns:
            Dictionary with the latest dashboard structure
        """
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
        '''Get the most recent date with dashboard data.

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
    async def get_log_json(result_id: int, page: int | None = None) -> dict:
        '''Get JSON log content for a test result.

        Fetches the actual JSON log data and attachments for a test result.

        Args:
            result_id: The ID of the test result
            page: Optional page number (0 for all pages combined, >0 for specific page)

        Returns:
            Dictionary with log content, attachments, and source URLs
        '''
        return await sync_to_async(LogService.get_log_json)(result_id, page)

    @mcp.tool()
    async def get_log_urls(result_id: int, page: int | None = None) -> dict:
        """Get log URLs for a test result (without fetching content).

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

    @mcp.tool()
    async def get_log_html_url(result_id: int) -> str | None:
        '''Get HTML log URL for a test result.

        Args:
            result_id: The ID of the test result

        Returns:
            URL string or None if not available
        '''
        return await sync_to_async(LogService.get_html_log_url)(result_id)

    # Tree tools

    @mcp.tool()
    async def get_tree(run_id: int) -> dict:
        '''Get test tree structure for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with tree structure (linear dict) and main_package ID
        '''
        return await sync_to_async(TreeService.get_tree)(run_id)

    @mcp.tool()
    async def get_tree_path(result_id: int) -> list[int]:
        '''Get path to a specific test result in the tree.

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
        """Get test history (linear format).

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
        """Get test history grouped by iteration.

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

    @mcp.tool()
    async def get_run_requirements(run_id: int) -> list[str]:
        '''Get requirements for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Sorted list of requirement strings
        '''
        return await sync_to_async(RunService.get_run_requirements)(run_id)

    @mcp.tool()
    async def get_run_comment(run_id: int) -> str | None:
        '''Get comment for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Comment string or None if no comment exists
        '''
        return await sync_to_async(RunService.get_run_comment)(run_id)

    # Server tools

    @mcp.tool()
    async def get_server_version() -> dict:
        '''Get server version information.

        Returns:
            Dictionary with repository revision information
        '''
        return await sync_to_async(ServerService.get_version)()

    # Report tools

    @mcp.tool()
    async def get_report_configs(run_id: int) -> list[dict]:
        '''Get available report configurations for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            List of available report config dictionaries
        '''
        run = await sync_to_async(ReportService.get_run)(run_id)
        return await sync_to_async(ReportService.get_configs_for_run_report)(run)

    @mcp.tool()
    async def get_report(run_id: int, config_id: int) -> dict:
        '''Generate full report for a run using specified config.

        Returns complete report with warnings, config, content, and unprocessed iterations.

        Args:
            run_id: The ID of the test run
            config_id: The ID of the report config

        Returns:
            Dictionary with full report data (warnings, config, content, unprocessed_iters)

        Raises:
            ValidationError: if run not found, config not found, or config validation fails
        '''
        return await sync_to_async(ReportService.generate_report)(run_id, config_id)
