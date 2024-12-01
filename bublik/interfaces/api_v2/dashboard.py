# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing
from typing import Iterable

from django.db.models import F
from django.db.models.fields import DateField
from django.db.models.functions import Cast
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.cache import RunCache
from bublik.core.config.services import getattr_from_per_conf
from bublik.core.importruns.live.check import livelog_check_run_timeout
from bublik.core.run.external_links import get_sources
from bublik.core.run.stats import (
    get_run_conclusion,
    get_run_stats,
    get_run_status,
    get_run_status_by_nok,
)
from bublik.core.utils import dicts_groupby, get_difference
from bublik.data.models import Meta, TestIterationResult


__all__ = [
    'DashboardFormatting',
    'DashboardPayload',
    'DashboardViewSet',
]


class DashboardViewSet(RetrieveModelMixin, GenericViewSet):

    required_settings: typing.ClassVar[list] = ['DASHBOARD_HEADER']
    extended_data: typing.ClassVar[list] = ['total', 'total_expected', 'progress', 'unexpected']
    available_column_modes: typing.ClassVar['dict'] = {
        'one_day_one_column': {'days': 1, 'columns': 1},
        'one_day_two_columns': {'days': 1, 'columns': 2},
        'two_days_two_columns': {'days': 2, 'columns': 2},
    }

    def get_queryset(self):
        self.payload = DashboardPayload()
        self.check_and_apply_settings()

        if self.date_meta:
            return (
                TestIterationResult.objects.filter(
                    test_run=None,
                    meta_results__meta__name=self.date_meta,
                    meta_results__meta__value=self.date,
                )
                .prefetch_related('meta_results')
                .distinct()
            )
        return (
            TestIterationResult.objects.filter(test_run=None, start__date=self.date)
            .prefetch_related('meta_results')
            .distinct()
        )

    @method_decorator(never_cache)
    def list(self, request):
        '''
        Return dashboard data for one day.
        Route: /api/v2/dashboard/?date=yyyy-mm-dd.
        '''
        if not self.prepare_settings():
            message = ', '.join(self.errors)
            return Response(data={'message': message}, status=status.HTTP_400_BAD_REQUEST)

        self.date = request.GET.get('date', self.get_latest_run_date())

        if not self.date:
            return Response(status=status.HTTP_204_NO_CONTENT)

        runs = self.get_queryset()

        if not runs:
            return Response(status=status.HTTP_204_NO_CONTENT)

        rows_data = []
        for run in runs:
            conclusion, conclusion_reason = get_run_conclusion(run)
            row_data = self.prepare_row_data(run)
            rows_data.append(
                {
                    'row_cells': row_data,
                    'context': {
                        'run_id': run.id,
                        'start': run.start.timestamp(),
                        'status': get_run_status(run),
                        'status_by_nok': get_run_status_by_nok(run)[0],
                        'conclusion': conclusion,
                        'conclusion_reason': conclusion_reason,
                    },
                },
            )

        rows_data.sort(key=lambda x: self.runs_sort(x))

        response = {
            'date': self.date,
            'rows': rows_data,
            'header': [{'key': k, 'name': n} for k, n in self.header.items()],
            'payload': self.payload.description,
        }

        return Response(data=response, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def default_mode(self, request):
        if not self.prepare_settings():
            message = ', '.join(self.errors)
            return Response(data={'message': message}, status=status.HTTP_400_BAD_REQUEST)
        mode = self.available_column_modes.get(self.default_mode)
        if not mode:
            message = 'Error in per-project configuration'
            return Response(
                data={'message': message},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(data={'mode': mode}, status=status.HTTP_200_OK)

    def prepare_settings(self):
        self.payload = DashboardPayload()
        self.formatting = DashboardFormatting()
        return self.check_and_apply_settings()

    def apply_if_match_header(self, setting, default=None, ignore=None, keys=False):
        data = getattr_from_per_conf(setting, default=default).copy()

        comparable = data.keys() if keys else data

        diff = get_difference(comparable, self.header.keys(), ignore)
        if diff:
            self.errors.append(f"{setting} doesn't match DASHBOARD_HEADER, mismatch: {diff}")

        return data

    def check_and_apply_settings(self):
        # Mandatory settings (must be defined in per_conf global config object):
        for setting in self.required_settings:
            getattr_from_per_conf(setting, required=True)

        header = getattr_from_per_conf('DASHBOARD_HEADER', required=True)
        self.header = {item['key']: item['label'] for item in header}
        self.date_meta = getattr_from_per_conf('DASHBOARD_DATE')
        self.default_mode = getattr_from_per_conf(
            'DASHBOARD_DEFAULT_MODE',
            default='two_days_two_columns',
        )

        self.errors = []
        if getattr(self, 'header', None):
            # Default settings (can be changed in per_conf global config object):
            self.sort = self.apply_if_match_header(
                'DASHBOARD_RUNS_SORT',
                default=['start'],
                ignore=['start'],
            )

            # Optional settings (can be defined in per_conf global config object):
            self.payload_settings = self.apply_if_match_header(
                'DASHBOARD_PAYLOAD',
                default={},
                keys=True,
            )

            self.formatting_settings = self.apply_if_match_header(
                'DASHBOARD_FORMATTING',
                default={'progress': 'percent'},
                keys=True,
                ignore=['progress'],
            )

            self.payload(self.payload_settings)
            self.errors.extend(self.payload.errors)
            self.formatting.apply_settings(self.formatting_settings)
            self.errors.extend(self.formatting.errors)

        return bool(not self.errors)

    def get_latest_run_date(self):
        if self.date_meta:
            date = (
                Meta.objects.filter(name=self.date_meta)
                .annotate(date=Cast(F('value'), DateField()))
                .order_by('-date')
                .values_list(F('date'), flat=True)
            ).first()
            if date:
                return date
        else:
            all_runs = TestIterationResult.objects.filter(test_run=None).order_by('-start')
            latest_run = all_runs.first()
            if latest_run:
                return latest_run.start.date()
        return None

    def normalize_values(self, row_data):
        for key in self.header:
            data = row_data.get(key)
            if data:
                row_data[key] = data[0] if len(data) == 1 else data
        return row_data

    def add_payload(self, row_data, run):
        for key in self.header:
            if key in self.payload.handlers:
                self.payload.handlers[key](row_data[key], run)
        return row_data

    def add_formatting(self, row_data):
        for key in self.header:
            if key in self.formatting.handlers:
                self.formatting.handlers[key](row_data[key])
        return row_data

    def prepare_row_data(self, run):
        livelog_check_run_timeout(run)
        cache = RunCache.by_obj(run, 'dashboard-v2')
        row_data = cache.data
        if not row_data:
            # Get all meta data for the row
            metabased_data = self.get_metabased_data(run)
            # Build cells for every row accessed by the header key
            row_data = {}
            for key, category in self.header.items():
                if key not in self.extended_data:
                    row_data[key] = metabased_data.get(category, [])
                else:
                    row_data[key] = self.get_extended_data(run, key)
            self.add_formatting(row_data)

        # Should not be cached, as can be changed by users any time
        self.add_payload(row_data, run)

        # To support multiple values as key-list and one value as key-value.
        self.normalize_values(row_data)

        return row_data

    def get_metabased_data(self, run):
        raw_data = list(
            run.meta_results.select_related('meta')
            .filter(meta__category__name__in=self.header.values())
            .annotate(category=F('meta__category__name'), value=F('meta__value'))
            .values('category', 'value'),
        )
        # Example of metabased_data: {'Session': ['loni'], 'Branch': ['master'], 'DL': ['OK'], }
        return dict(dicts_groupby(raw_data, 'category'))

    def get_extended_data(self, run, key):
        data = {'value': get_run_stats(run.id).get(key, '')}
        return [data]

    def prepare_sort_key(self, row, key):
        if key == 'start':
            return row['context']['start']

        cell_data = row['row_cells'][key]
        if cell_data and 'value' in cell_data:
            return cell_data['value']

        if isinstance(cell_data, Iterable):
            values = []
            for item in cell_data:
                values.append(item['value'])
            return ','.join(values)

        return ''

    def runs_sort(self, row):
        sort_keys = []
        for key in self.sort:
            sort_keys.append(self.prepare_sort_key(row, key))
        return sort_keys


class DashboardFormatting:
    '''
    Applies formatting rules to particular values displayed on the dashboard.
    '''

    handlers: typing.ClassVar['dict'] = {}
    handlers_available: typing.ClassVar['list'] = [
        'percent',
    ]

    def apply_settings(self, settings):
        if not self.__match_settings_to_methods(settings.values()):
            return

        for rule, method in settings.items():
            self.handlers[rule] = getattr(self, method)

    def __match_settings_to_methods(self, settings):
        self.errors = []
        diff = get_difference(settings, self.handlers_available)
        if diff:
            self.errors.append(f"Unknown value(s) in DASHBOARD_FORMATTING: {', '.join(diff)}")
            return False
        return True

    def percent(self, data):
        for item in data:
            value = item['value']
            if isinstance(value, (int, float)):
                item.update(
                    {
                        'value': str(round(value * 100)) + '%',
                    },
                )
        return data


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
