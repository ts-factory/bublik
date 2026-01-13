# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.forms.models import model_to_dict

from bublik.core.report.components import ReportPoint, ReportTestLevel
from bublik.core.utils import parse_number, unordered_group_by
from bublik.data.models import (
    Config,
    MeasurementResult,
    TestArgument,
    TestIterationResult,
)
from bublik.data.models.result import ResultType
from bublik.data.serializers import ConfigSerializer


def get_common_args(mmrs_test):
    '''
    Collect arguments that have the same values for all iterations of the test
    with the passed name within the passed package.
    '''
    mmrs_test_ids = mmrs_test.values_list('id', flat=True)

    return dict(
        TestArgument.objects.filter(
            test_iterations__testiterationresult__measurement_results__id__in=mmrs_test_ids,
        )
        .annotate(
            test_arg_count=Count(
                'test_iterations',
                filter=Q(
                    test_iterations__testiterationresult__measurement_results__id__in=mmrs_test_ids,
                ),
            ),
        )
        .filter(test_arg_count=len(mmrs_test_ids))
        .values_list('name', 'value'),
    )


def filter_by_axis_y(mmrs_test, axis_y):
    '''
    Filter passed measurement results QS by axis y value from config.
    '''
    mmrs_test_axis_y = MeasurementResult.objects.none()
    for measurement in axis_y:
        mmrs_test_measurement = mmrs_test.all()
        # filter by tool
        if 'tool' in measurement:
            tools = measurement.pop('tool')
            mmrs_test_measurement = mmrs_test.filter(
                measurement__metas__name='tool',
                measurement__metas__type='tool',
                measurement__metas__value__in=tools,
            )

        # filter by keys
        if 'keys' in measurement:
            meas_key_mmrs = MeasurementResult.objects.none()
            keys_vals = measurement.pop('keys')
            for key_name, key_vals in keys_vals.items():
                meas_key_mmrs_group = mmrs_test_measurement.filter(
                    measurement__metas__name=key_name,
                    measurement__metas__type='measurement_key',
                    measurement__metas__value__in=key_vals,
                )
                meas_key_mmrs = meas_key_mmrs.union(meas_key_mmrs_group)
            mmrs_test_measurement = meas_key_mmrs

        # filter by measurement subjects (type, name, aggr)
        for ms, ms_values in measurement.items():
            mmrs_test_measurement = mmrs_test_measurement.filter(
                measurement__metas__name=ms,
                measurement__metas__type='measurement_subject',
                measurement__metas__value__in=ms_values,
            )
        mmrs_test_axis_y = mmrs_test_axis_y.union(mmrs_test_measurement)

    # the union will be impossible to filter out
    mmrs_test_axis_y_ids = mmrs_test_axis_y.values_list('id', flat=True)
    return mmrs_test.filter(id__in=mmrs_test_axis_y_ids)


def filter_by_not_show_args(mmrs_test, not_show_args):
    '''
    Drop measurement results corresponding to iterations with the passed
    arguments values from the passed measurement results QS.
    '''
    not_show_mmrs = MeasurementResult.objects.none()
    for arg, vals in not_show_args.items():
        arg_vals_mmrs = mmrs_test.filter(
            result__iteration__test_arguments__name=arg,
            result__iteration__test_arguments__value__in=vals,
        )
        not_show_mmrs = not_show_mmrs.union(arg_vals_mmrs)

    return mmrs_test.difference(not_show_mmrs)


class ReportService:
    '''Service for report generation operations (shared between REST API and MCP).'''

    @staticmethod
    def get_run(run_id: int) -> TestIterationResult:
        '''Get a run by ID.

        Args:
            run_id: The ID of the test run

        Returns:
            TestIterationResult instance

        Raises:
            ValidationError: if run not found
        '''
        try:
            return TestIterationResult.objects.get(id=run_id)
        except TestIterationResult.DoesNotExist as e:
            msg = f'Run {run_id} not found'
            raise ValidationError(msg) from e

    @staticmethod
    def get_report_config(config_id: int) -> tuple[Config, dict, dict]:
        '''Get and validate a report configuration.

        Args:
            config_id: The ID of the report config

        Returns:
            Tuple of (config_obj, config_data, config_content)

        Raises:
            ValidationError: if config not found or validation fails
        '''
        try:
            report_config_obj = Config.objects.get(id=config_id)
        except Config.DoesNotExist as e:
            msg = f'Config {config_id} not found'
            raise ValidationError(msg) from e

        config_data = model_to_dict(
            report_config_obj,
            fields=['name', 'description', 'version'],
        )
        report_config = report_config_obj.content

        # Validate config content
        serializer = ConfigSerializer(report_config_obj, {'content': report_config})
        serializer.validate_content(report_config)

        return report_config_obj, config_data, report_config

    @staticmethod
    def get_configs_for_run_report(run) -> list[dict]:
        '''Get available report configurations for a run.

        Args:
            run: TestIterationResult instance

        Returns:
            List of available report config dictionaries
        '''
        iters = TestIterationResult.objects.filter(test_run=run)
        test_names = list(
            iters.filter(iteration__test__result_type=ResultType.conv(ResultType.TEST))
            .distinct('iteration__test__name')
            .values_list(
                'iteration__test__name',
                flat=True,
            ),
        )

        active_report_configs = Config.objects.filter(
            type='report',
            project_id=run.project.id,
            is_active=True,
        )

        run_report_configs = []
        for report_config in active_report_configs:
            report_config_content = report_config.content
            # skip invalid
            if 'test_names_order' not in report_config_content:
                continue
            report_config_test_names = report_config_content['test_names_order']
            if set(report_config_test_names).intersection(test_names):
                run_report_configs.append(
                    model_to_dict(
                        report_config,
                        exclude=['type', 'is_active', 'user', 'content'],
                    ),
                )

        return run_report_configs

    @staticmethod
    def generate_report(run_id: int, config_id: int) -> dict:
        '''Generate full report for a run using specified config.

        Args:
            run_id: The ID of the test run
            config_id: The ID of the report config

        Returns:
            Dictionary with warnings, config, content, unprocessed_iters

        Raises:
            ValidationError: if run not found, config not found,
                           or config validation fails
        '''
        warnings = []

        # Get run
        run = ReportService.get_run(run_id)
        main_pkg = run.root

        # Get and validate config
        _, config_data, report_config = ReportService.get_report_config(config_id)

        # Get measurement results
        mmrs_run = MeasurementResult.objects.filter(
            result__test_run=main_pkg,
        )

        # Process tests in config
        common_args = {}
        mmrs_report = MeasurementResult.objects.none()

        for test_name, test_config in report_config['tests'].items():
            # Filter by test name
            mmrs_test = mmrs_run.filter(result__iteration__test__name=test_name)
            if not mmrs_test:
                continue

            # Filter by axis_y
            axis_y = test_config['axis_y']
            mmrs_test = filter_by_axis_y(mmrs_test, axis_y)
            if not mmrs_test:
                msg = (
                    f'{test_name} test: no results after filtering by axis_y value. '
                    'Fix report configuration'
                )
                warnings.append(msg)
                continue

            # Filter by not_show_args
            not_show_args = test_config['not_show_args']
            mmrs_test = filter_by_not_show_args(mmrs_test, not_show_args)
            if not mmrs_test:
                msg = (
                    f'{test_name} test: no results after filtering by not_show_args value. '
                    'Fix report configuration'
                )
                warnings.append(msg)
                if test_name in report_config['test_names_order']:
                    report_config['test_names_order'].remove(test_name)
                continue

            # Collect common args
            common_args[test_name] = get_common_args(mmrs_test)

            mmrs_report = mmrs_report.union(mmrs_test)

        mmrs_report = mmrs_report.order_by('id')

        # Build report points
        points = []
        unprocessed_iters = []

        for mmr in mmrs_report:
            try:
                points.append(ReportPoint(mmr, common_args, report_config))
            except ValueError as ve:
                test_name = mmr.result.iteration.test.name
                common_test_args = common_args[test_name]
                invalid_iteration = {
                    'test_name': test_name,
                    'common_args': common_test_args,
                    'args_vals': {
                        arg.name: parse_number(arg.value)
                        for arg in mmr.result.iteration.test_arguments.all()
                        if arg.name not in common_test_args
                    },
                    'reasons': ve.args[0],
                }
                if invalid_iteration not in unprocessed_iters:
                    unprocessed_iters.append(invalid_iteration)

        # Group points into records
        content = []
        points_by_test_names = unordered_group_by(points, 'test_name')
        if report_config['test_names_order']:
            points_by_test_names = ReportPoint.by_test_name_sort(
                points_by_test_names,
                report_config['test_names_order'],
            )

        for test_name, test_points in points_by_test_names.items():
            test = ReportTestLevel(test_name, common_args, list(test_points), report_config)
            content.append(test.__dict__)

        return {
            'warnings': warnings,
            'config': config_data,
            'content': content,
            'unprocessed_iters': unprocessed_iters,
        }
