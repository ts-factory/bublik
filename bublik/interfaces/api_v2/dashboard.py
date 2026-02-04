# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.config.services import ConfigServices
from bublik.core.dashboard import DashboardService
from bublik.core.report.services import ReportService
from bublik.core.run.external_links import get_sources
from bublik.core.utils import get_difference
from bublik.data.models import GlobalConfigs, TestIterationResult


__all__ = [
    'DashboardPayload',
    'DashboardViewSet',
]


class DashboardViewSet(RetrieveModelMixin, GenericViewSet):
    available_column_modes: typing.ClassVar['dict'] = {
        'one_day_one_column': {'days': 1, 'columns': 1},
        'one_day_two_columns': {'days': 1, 'columns': 2},
        'two_days_two_columns': {'days': 2, 'columns': 2},
    }

    @method_decorator(never_cache)
    def list(self, request):
        '''
        Return dashboard data for one day.
        Route: /api/v2/dashboard/?date=yyyy-mm-dd.
        '''
        project_id = request.query_params.get('project')
        if not self.prepare_settings(project_id):
            message = ', '.join(self.errors)
            return Response(data={'message': message}, status=status.HTTP_400_BAD_REQUEST)

        # Get date from request or use latest available
        date = request.GET.get('date')
        if not date:
            date = DashboardService.get_latest_dashboard_date(project_id)

        if not date:
            return Response(status=status.HTTP_204_NO_CONTENT)

        data = DashboardService.get_dashboard_data(
            date,
            project_id,
            header=self.header,
            formatting_settings=self.formatting_settings,
            sort_config=self.sort,
        )

        # Apply payload handlers for URL generation (UI-specific)
        self._apply_payload_to_dashboard_data(data)
        data['payload'] = self.payload.description

        return Response(data=data)

    @action(detail=False, methods=['get'])
    def default_mode(self, request):
        project_id = request.query_params.get('project')
        if not self.prepare_settings(project_id):
            message = ', '.join(self.errors)
            return Response(data={'message': message}, status=status.HTTP_400_BAD_REQUEST)
        mode = self.available_column_modes.get(self.default_mode)
        if not mode:
            message = 'Error in per-project configuration'
            return Response(
                data={'message': message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(data={'mode': mode})

    def prepare_settings(self, project_id):
        self.payload = DashboardPayload()
        return self.check_and_apply_settings(project_id)

    def check_and_apply_settings(self, project_id):
        # Use service validation for all dashboard settings
        validation = DashboardService.validate_dashboard_settings(
            project_id,
            raise_on_error=False,  # Get errors for ViewSet compatibility
        )

        if not validation['valid']:
            self.errors = validation['errors']
            return False

        # Load settings (now known to be valid)
        header = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_HEADER',
            project_id,
        )
        self.header = {item['key']: item['label'] for item in header}
        self.date_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_DATE',
            project_id,
        )
        self.default_mode = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_DEFAULT_MODE',
            project_id,
        )

        # Load optional settings
        self.sort = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_RUNS_SORT',
            project_id,
            default=['start'],
        )
        self.payload_settings = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_PAYLOAD',
            project_id,
            default={},
        )
        self.formatting_settings = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_FORMATTING',
            project_id,
            default={'progress': 'percent'},
        )

        # Initialize payload (remains in ViewSet - UI-specific)
        self.payload(self.payload_settings)
        self.errors = self.payload.errors

        return bool(not self.errors)

    def _extract_payload(self, processed):
        '''
        Extract payload from processed handler result.

        Args:
            processed: Result from payload handler (list or dict)

        Returns:
            Payload dict or None if no payload found
        '''
        if isinstance(processed, list) and processed and 'payload' in processed[0]:
            return processed[0]['payload']
        if isinstance(processed, dict) and 'payload' in processed:
            return processed['payload']
        return None

    def _prepare_handler_data(self, cell_data):
        '''
        Prepare cell data for payload handler consumption.

        Payload handlers expect data in specific formats (list of dicts
        or dict with 'value' key).
        This normalizes various cell_data formats into the expected structure.

        Args:
            cell_data: Raw cell data from dashboard

        Returns:
            Normalized data structure for payload handler
        '''
        if isinstance(cell_data, dict):
            return [cell_data]
        if isinstance(cell_data, list):
            if cell_data and isinstance(cell_data[0], (str, int, float)):
                return [{'value': v} for v in cell_data]
            return cell_data
        # Single primitive value
        return {'value': cell_data}

    def _merge_payload_to_cell(self, row, key, cell_data, processed):
        '''
        Merge payload from processed handler result back into cell data.

        Args:
            row: Dashboard row dict
            key: Cell key within the row
            cell_data: Original cell data reference
            processed: Processed data from payload handler
        '''
        payload = self._extract_payload(processed)
        if not payload:
            return

        if isinstance(cell_data, dict):
            cell_data['payload'] = payload
        elif isinstance(cell_data, (str, int, float)):
            row['row_cells'][key] = {'value': cell_data, 'payload': payload}
        elif isinstance(cell_data, list) and cell_data:
            if isinstance(cell_data[0], dict):
                cell_data[0]['payload'] = payload
            elif isinstance(cell_data[0], (str, int, float)):
                cell_data[0] = {'value': cell_data[0], 'payload': payload}

    def _apply_payload_to_dashboard_data(self, data: dict) -> None:
        '''
        Apply payload handlers to dashboard cell data for URL generation.

        This is a UI-specific operation that adds navigation URLs to dashboard cells.
        The payload handlers in DashboardPayload generate URLs for run details,
        tree views, source links, etc.

        Args:
            data: Dashboard data dictionary with 'rows' key containing row data.
                  Modified in-place to add payload information.
        '''
        if not hasattr(self, 'payload') or not self.payload.handlers:
            return

        for row in data.get('rows', []):
            run_id = row['context']['run_id']
            run = TestIterationResult.objects.get(id=run_id)

            for key in row['row_cells']:
                if key not in self.payload.handlers or 'context' not in row:
                    continue

                cell_data = row['row_cells'][key]
                handler_data = self._prepare_handler_data(cell_data)

                # Apply payload handler
                processed = self.payload.handlers[key](handler_data, run)

                # Merge payload back into cell data
                self._merge_payload_to_cell(row, key, cell_data, processed)


class DashboardPayload:
    '''
    Wraps dashboard data with the payload defined in DASHBOARD_PAYLOAD.

    Payload provides:
    - functional handlers used in DashboardView
      to have button-links for chosen columns;
    - per-project system for giving style to rows;
      style is controlled by css classes and can depend on data in rows.
    '''

    handlers: typing.ClassVar['dict'] = {}
    handlers_available: typing.ClassVar['dict'] = {
        'go_run': 'go to run details',
        'go_run_failed': 'go to run details with failed results opened',
        'go_tree': 'go to run tests as a tree with its logs and context',
        'go_bug': 'go to bug in the repository',
        'go_source': 'go to source from which the run was imported',
        'go_report': 'go to the most recent report',
    }

    def __call__(self, settings):
        if not self.__match_settings_to_methods(settings.values()):
            return self.errors

        self.__prepare_handlers(settings)
        self.__prepare_payload_description(settings)

        return self

    def __match_settings_to_methods(self, settings):
        self.errors = []
        diff = get_difference(settings, self.handlers_available.keys())
        if diff:
            self.errors.append(f"Unknown value(s) in DASHBOARD_PAYLOAD: {', '.join(diff)}")
        return bool(not self.errors)

    def __prepare_handlers(self, settings):
        for key in settings:
            method = getattr(self, settings[key])
            self.handlers[key] = method

    def __prepare_payload_description(self, settings):
        self.description = {}
        for key, payload in settings.items():
            self.description[key] = self.handlers_available[payload]

    def go_run(self, data, run):
        for item in data:
            item.update(
                {
                    'payload': {
                        'url': 'runs',
                        'params': run.id,
                    },
                },
            )
        return data

    def go_run_failed(self, data, run):
        # Should be updated, no link-functionality available.
        for item in data:
            item.update(
                {
                    'payload': {
                        'url': 'runs',
                        'params': run.id,
                    },
                },
            )
        return data

    def go_tree(self, data, run):
        for item in data:
            item.update(
                {
                    'payload': {
                        'url': 'tree',
                        'params': run.id,
                    },
                },
            )
        return data

    def go_bug(self, data, run):
        # TODO: In the new interface bugs will be display in a different place.
        return data

    def go_source(self, data, run):
        for item in data:
            item.update(
                {
                    'payload': {
                        'url': get_sources(run),
                    },
                },
            )

    def go_report(self, data, run):
        run_report_configs_data = ReportService.get_configs_for_run_report(run)
        if run_report_configs_data:
            # get the ID of the most recent applicable config
            cfg_id = max(run_report_configs_data, key=lambda cfg_data: cfg_data['id'])['id']
            for item in data:
                item.update(
                    {
                        'payload': {
                            'url': 'runs',
                            'params': f'{run.id}/report/?config={cfg_id}',
                        },
                    },
                )
