# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import F
from django.db.models.fields import DateField
from django.db.models.functions import Cast

from bublik.core.cache import RunCache
from bublik.core.config.services import ConfigServices
from bublik.core.datetime_formatting import date_str_to_db
from bublik.core.importruns.live.check import livelog_check_run_timeout
from bublik.core.run.stats import (
    get_run_conclusion,
    get_run_stats,
    get_run_status,
    get_run_status_by_nok,
)
from bublik.core.utils import dicts_groupby, get_difference
from bublik.data import models
from bublik.data.models import GlobalConfigs


class DashboardService:
    @staticmethod
    def get_dashboard_data(
        date: str,
        project_id: int | None = None,
        header: dict | None = None,
        formatting_settings: dict | None = None,
        sort_config: list | None = None,
    ) -> dict:
        """Get dashboard data for a specific date.

        Args:
            date: Date string in 'yyyy-mm-dd' format
            project_id: Optional project ID to filter by
            header: Optional header dict mapping keys to labels (fetched from config if None)
            formatting_settings: Optional dict mapping keys to formatting methods
            sort_config: Optional list of column keys to sort by

        Returns:
            Dictionary with date, rows, header, and payload

        Raises:
            ValidationError: if date format is invalid or config missing
        """

        # Get dashboard header configuration (required) if not provided
        if header is None:
            header_config = ConfigServices.getattr_from_global(
                GlobalConfigs.PER_CONF.name,
                'DASHBOARD_HEADER',
                project_id,
            )
            header = {item['key']: item['label'] for item in header_config}

        # Get date meta setting (optional)
        date_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_DATE',
            project_id,
        )

        # Get runs for the date
        if date_meta:
            runs = models.TestIterationResult.objects.filter(
                test_run=None,
                meta_results__meta__name=date_meta,
                meta_results__meta__value=date,
            )
        else:
            db_date = date_str_to_db(date)
            runs = models.TestIterationResult.objects.filter(
                test_run=None,
                start__date=db_date,
            )

        if project_id:
            runs = runs.filter(project_id=project_id)

        runs = runs.select_related('project').prefetch_related('meta_results').distinct()

        # Build rows
        rows_data = []
        for run in runs:
            conclusion, conclusion_reason = get_run_conclusion(run)
            row_cells = DashboardService.prepare_row_data(run, header, project_id)
            rows_data.append(
                {
                    'row_cells': row_cells,
                    'context': {
                        'run_id': run.id,
                        'project_id': run.project.id,
                        'project_name': run.project.name,
                        'start': run.start.timestamp(),
                        'status': get_run_status(run),
                        'status_by_nok': get_run_status_by_nok(run)[0],
                        'conclusion': conclusion,
                        'conclusion_reason': conclusion_reason,
                    },
                },
            )

        # Apply formatting if provided
        if formatting_settings:
            DashboardService._apply_formatting(rows_data, formatting_settings)

        # Apply sorting if provided
        if sort_config:
            DashboardService.sort_dashboard_rows(rows_data, sort_config)

        return {
            'date': date,
            'rows': rows_data,
            'header': [{'key': k, 'name': n} for k, n in header.items()],
            'payload': {},  # Simplified for MCP (no URL handlers needed)
        }

    @staticmethod
    def apply_dashboard_formatting(rows_data: list, formatting_settings: dict):
        """Apply formatting rules to dashboard row cells.

        This is a public method that validates formatting methods before applying them.
        Available formatters: 'percent'.

        Args:
            rows_data: List of row dictionaries with 'row_cells' key
            formatting_settings: Dict mapping keys to formatting method names
                                 (e.g., {'progress': 'percent'})

        Raises:
            ValidationError: if unknown formatting method specified
        """
        available_formatters = {'percent'}

        # Validate formatting methods
        unknown_methods = set(formatting_settings.values()) - available_formatters
        if unknown_methods:
            msg = (
                f"Unknown formatting method(s): {', '.join(unknown_methods)}. "
                f"Available: {', '.join(available_formatters)}"
            )
            raise ValidationError(msg)

        for row in rows_data:
            for key, method_name in formatting_settings.items():
                if key in row['row_cells']:
                    cell_data = row['row_cells'][key]
                    if method_name == 'percent':
                        DashboardService._format_percent(cell_data)

    @staticmethod
    def _apply_formatting(rows_data: list, formatting_settings: dict):
        """Apply formatting rules to row cells.

        Internal method that does not validate. Use apply_dashboard_formatting()
        for public access with validation.

        Args:
            rows_data: List of row dictionaries with 'row_cells' key
            formatting_settings: Dict mapping keys to formatting method names
        """
        for row in rows_data:
            for key, method_name in formatting_settings.items():
                if key in row['row_cells']:
                    cell_data = row['row_cells'][key]
                    if method_name == 'percent':
                        DashboardService._format_percent(cell_data)

    @staticmethod
    def sort_dashboard_rows(rows_data: list, sort_config: list) -> None:
        """Sort dashboard rows in-place by the configured columns.

        Args:
            rows_data: List of row dictionaries with 'row_cells' and 'context' keys
            sort_config: List of column keys to sort by (e.g., ['start', 'total'])

        The rows_data list is modified in-place.
        """

        def prepare_sort_key(row: dict, key: str):
            if key == 'start':
                return row['context']['start']

            cell_data = row['row_cells'].get(key)
            if cell_data and isinstance(cell_data, dict) and 'value' in cell_data:
                return cell_data['value']

            if isinstance(cell_data, list):
                values = []
                for item in cell_data:
                    if isinstance(item, dict) and 'value' in item:
                        values.append(item['value'])
                return ','.join(str(v) for v in values)

            return ''

        rows_data.sort(key=lambda row: [prepare_sort_key(row, key) for key in sort_config])

    @staticmethod
    def _format_percent(data):
        """Format a value as a percentage.

        Args:
            data: Cell data (dict or list of dicts) with 'value' key
        """

        def format_value(item):
            value = item.get('value')
            if isinstance(value, (int, float)):
                item['value'] = str(round(value * 100)) + '%'
            return item

        if isinstance(data, dict):
            format_value(data)
        elif isinstance(data, list):
            for item in data:
                format_value(item)

    @staticmethod
    def prepare_row_data(run, header, project_id):
        '''Prepare row cells data for a run.

        This is the single source of truth for dashboard row preparation.
        Shared between REST API DashboardViewSet and MCP tools.

        Args:
            run: TestIterationResult instance
            header: Dictionary mapping column keys to category names
            project_id: Project ID for category filtering

        Returns:
            Dictionary with row cells data
        '''

        livelog_check_run_timeout(run)

        # Try cache first
        cache = RunCache.by_obj(run, 'dashboard-v2')
        row_data = cache.data

        if not row_data:
            # Get metabased data
            metabased_data = list(
                run.meta_results.select_related('meta')
                .filter(
                    meta__category__name__in=header.values(),
                    meta__category__project_id=run.project.id,
                )
                .annotate(category=F('meta__category__name'), value=F('meta__value'))
                .values('category', 'value'),
            )
            metabased_dict = dict(dicts_groupby(metabased_data, 'category'))

            # Build cells
            row_data = {}
            extended_keys = ['total', 'total_expected', 'progress', 'unexpected']
            for key, category in header.items():
                if key not in extended_keys:
                    row_data[key] = metabased_dict.get(category, [])
                else:
                    stats = get_run_stats(run.id)
                    row_data[key] = [{'value': stats.get(key, '')}]

        # Normalize values (unwrap single-item lists)
        for key in list(row_data.keys()):
            data = row_data.get(key)
            if data and len(data) == 1:
                row_data[key] = data[0]

        return row_data

    @staticmethod
    def get_latest_dashboard_date(project_id: int | None = None) -> str | None:
        """Get the most recent date with dashboard data.

        Args:
            project_id: Optional project ID to filter by

        Returns:
            Date string in 'yyyy-mm-dd' format, or None
        """

        date_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_DATE',
            project_id,
        )

        if date_meta:
            dates = models.Meta.objects.filter(name=date_meta)

            if project_id:
                dates = dates.filter(metaresult__result__project_id=project_id)

            date_result = (
                dates.annotate(date=Cast(F('value'), DateField()))
                .order_by('-date')
                .values_list(F('date'), flat=True)
                .first()
            )
            return date_result.isoformat() if date_result else None

        runs = models.TestIterationResult.objects.filter(test_run=None)

        if project_id:
            runs = runs.filter(project_id=project_id)

        latest = runs.order_by('-start').first()

        return latest.start.date().isoformat() if latest else None

    @staticmethod
    def validate_dashboard_settings(
        project_id: int | None = None,
        raise_on_error: bool = False,
    ) -> dict:
        """Validate all dashboard configuration settings.

        Args:
            project_id: Optional project ID for per-project config
            raise_on_error: If True, raise ValidationError; if False, return errors dict

        Returns:
            If raise_on_error=False: dict with 'valid' bool and 'errors' list
            If raise_on_error=True: raises ValidationError on invalid settings

        Raises:
            ValidationError: if settings invalid and raise_on_error=True
        """
        errors = []

        # Get header (required setting)
        try:
            header_config = ConfigServices.getattr_from_global(
                GlobalConfigs.PER_CONF.name,
                'DASHBOARD_HEADER',
                project_id,
            )
            header = {item['key']: item['label'] for item in header_config}
        except Exception as e:
            errors.append(f'DASHBOARD_HEADER: {e}')

        if not errors:
            header_keys = set(header.keys())

            # Validate DASHBOARD_RUNS_SORT keys match header
            try:
                sort_config = ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'DASHBOARD_RUNS_SORT',
                    project_id,
                    default=['start'],
                )
                sort_keys = set(sort_config) - {'start'}  # 'start' is always valid
                diff = get_difference(sort_keys, header_keys)
                if diff:
                    errors.append(
                        f"DASHBOARD_RUNS_SORT doesn't match DASHBOARD_HEADER, mismatch: {diff}",
                    )
            except Exception as e:
                errors.append(f'DASHBOARD_RUNS_SORT: {e}')

            # Validate DASHBOARD_PAYLOAD keys match header
            try:
                payload_settings = ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'DASHBOARD_PAYLOAD',
                    project_id,
                    default={},
                )
                available_payload_handlers = {
                    'go_run',
                    'go_run_failed',
                    'go_tree',
                    'go_bug',
                    'go_source',
                    'go_report',
                }
                payload_keys = set(payload_settings.keys())
                diff = get_difference(payload_keys, header_keys)
                if diff:
                    errors.append(
                        f"DASHBOARD_PAYLOAD doesn't match DASHBOARD_HEADER, mismatch: {diff}",
                    )
                # Validate payload handler names
                diff_handlers = get_difference(
                    payload_settings.values(),
                    available_payload_handlers,
                )
                if diff_handlers:
                    errors.append(
                        f"Unknown value(s) in DASHBOARD_PAYLOAD: {', '.join(diff_handlers)}",
                    )
            except Exception as e:
                errors.append(f'DASHBOARD_PAYLOAD: {e}')

            # Validate DASHBOARD_FORMATTING
            try:
                formatting_settings = ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'DASHBOARD_FORMATTING',
                    project_id,
                    default={'progress': 'percent'},
                )
                available_formatters = {'percent'}
                formatting_keys = set(formatting_settings.keys())
                diff = get_difference(formatting_keys, header_keys)
                if diff:
                    errors.append(
                        "DASHBOARD_FORMATTING doesn't match DASHBOARD_HEADER, "
                        f"mismatch: {diff}",
                    )
                # Validate formatter method names
                diff_methods = get_difference(
                    formatting_settings.values(),
                    available_formatters,
                )
                if diff_methods:
                    errors.append(
                        f"Unknown value(s) in DASHBOARD_FORMATTING: {', '.join(diff_methods)}",
                    )
            except Exception as e:
                errors.append(f'DASHBOARD_FORMATTING: {e}')

        if errors:
            if raise_on_error:
                raise ValidationError(errors)
            return {'valid': False, 'errors': errors}

        return {'valid': True, 'errors': []}

    @staticmethod
    def get_latest_run_date(project_id: int | None = None) -> str | None:
        """Alias for get_latest_dashboard_date() for backward compatibility.

        Args:
            project_id: Optional project ID to filter by

        Returns:
            Date string in 'yyyy-mm-dd' format, or None
        """
        return DashboardService.get_latest_dashboard_date(project_id)
