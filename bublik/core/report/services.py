# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q, Subquery
from django.forms.models import model_to_dict
from rest_framework.exceptions import ValidationError

from bublik.core.exceptions import NotFoundError
from bublik.core.report.components import ReportPoint, ReportTestLevel
from bublik.core.run.services import RunService
from bublik.core.utils import parse_number, unordered_group_by
from bublik.data.models import (
    Config,
    Measurement,
    MeasurementResult,
    TestArgument,
    TestIterationResult,
)
from bublik.data.models.result import ResultType
from bublik.data.serializers import ConfigSerializer


def get_common_args(mmrs_test_ids):
    """
    Collect arguments that have the same values for all iterations of the test
    with the passed name within the passed package.
    """
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


def _measurement_meta_ids(meta_name, meta_type, meta_values, mmrs_test_meas_ids):
    """
    Return the set of measurement ids, limited to mmrs_test_meas_ids, that have
    a meta name/type with a value from meta_values.
    """
    return set(
        Measurement.objects.filter(
            id__in=mmrs_test_meas_ids,
            metas__name=meta_name,
            metas__type=meta_type,
            metas__value__in=meta_values,
        ).values_list('id', flat=True),
    )


def filter_by_axis_y(mmrs_test, axis_y):
    """
    Filter passed measurement result queryset by axis y value from config.
    Returns a plain list of ids.
    """
    if not axis_y:
        return []

    mmrs_test_meas_ids = set(mmrs_test.values_list('measurement_id', flat=True).distinct())
    if not mmrs_test_meas_ids:
        return []

    axis_y_meas_ids = set()

    for measurement in axis_y:
        measurement_ids = []

        # filter by tool
        if 'tool' in measurement:
            tools = measurement.pop('tool')
            measurement_ids.append(
                _measurement_meta_ids('tool', 'tool', tools, mmrs_test_meas_ids)
            )

        # filter by keys
        if 'keys' in measurement:
            keys_vals = measurement.pop('keys')
            keys_ids = set()
            for key_name, key_vals in keys_vals.items():
                keys_ids |= _measurement_meta_ids(
                    key_name,
                    'measurement_key',
                    key_vals,
                    mmrs_test_meas_ids,
                )
            measurement_ids.append(keys_ids)

        # filter by measurement subjects (type, name, aggr)
        for ms, ms_values in measurement.items():
            measurement_ids.append(
                _measurement_meta_ids(ms, 'measurement_subject', ms_values, mmrs_test_meas_ids),
            )

        if measurement_ids:
            axis_y_meas_ids |= set.intersection(*measurement_ids)

    if not axis_y_meas_ids:
        return []

    return list(
        mmrs_test.filter(measurement_id__in=axis_y_meas_ids).values_list('id', flat=True),
    )


def filter_by_not_show_args(mmrs_test, not_show_args):
    """
    Drop ids of measurement results corresponding to iterations with the
    passed arguments values. Returns a plain list of ids.
    """
    if not not_show_args:
        return list(mmrs_test.values_list('id', flat=True))

    not_show_args_q = Q()
    for arg, vals in not_show_args.items():
        arg_vals_mmrs_ids = mmrs_test.filter(
            result__iteration__test_arguments__name=arg,
            result__iteration__test_arguments__value__in=vals,
        ).values('pk')
        not_show_args_q |= Q(pk__in=Subquery(arg_vals_mmrs_ids))

    return list(mmrs_test.exclude(not_show_args_q).values_list('id', flat=True))


class ReportService:
    @staticmethod
    def get_report_config(config_id: int) -> tuple[Config, dict, dict]:
        """
        Get and validate a report configuration.

        Args:
            config_id: The ID of the report config

        Returns:
            Tuple of (config_obj, config_data, config_content)

        Raises:
            NotFoundError: if config not found
        """
        try:
            report_config_obj = Config.objects.get(id=config_id)
        except ValueError as e:
            msg = f'Invalid config ID: {config_id}'
            raise ValidationError(msg) from e
        except ObjectDoesNotExist as e:
            msg = f'Config {config_id} not found'
            raise NotFoundError(msg) from e

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
        """
        Get available report configurations for a run.

        Args:
            run: TestIterationResult instance

        Returns:
            List of available report config dictionaries
        """
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
            # a config is applicable to a run if it configures at least
            # one test that actually ran
            report_config_test_names = report_config_content.get('tests', {}).keys()
            if set(report_config_test_names).intersection(test_names):
                run_report_configs.append(
                    model_to_dict(
                        report_config,
                        exclude=['type', 'is_active', 'user', 'content'],
                    ),
                )

        return run_report_configs

    @staticmethod
    def get_most_recent_config_for_run_report(run) -> list[dict]:
        """
        Get the ID of the most recent available report configuration for a run.

        Args:
            run: TestIterationResult instance

        Returns:
            ID of the most recent report config if available,
            otherwise None if no configs exist.
        """

        run_report_configs_data = ReportService.get_configs_for_run_report(run)
        if run_report_configs_data:
            # get the ID of the most recent applicable config
            return max(run_report_configs_data, key=lambda cfg_data: cfg_data['id'])['id']
        return None

    @staticmethod
    def generate_report(run_id: int, config_id: int) -> dict:
        """
        Generate full report for a run using specified config.

        Args:
            run_id: The ID of the test run
            config_id: The ID of the report config

        Returns:
            Dictionary with warnings, config, content, unprocessed_iters

        Raises:
            NotFoundError: if run not found or config not found
        """
        warnings = []

        # Get run
        run = RunService.get_run(run_id)
        main_pkg = run.root

        # Get and validate config
        _, config_data, report_config = ReportService.get_report_config(config_id)

        # Get measurement results
        mmrs_run = (
            MeasurementResult.objects.filter(result__test_run=main_pkg)
            .select_related('measurement', 'result__iteration__test')
            .prefetch_related(
                'result__iteration__test_arguments',
                'measurement__metas',
            )
        )

        # Process tests in config
        common_args = {}
        report_q = Q()

        for test_name, test_config in report_config['tests'].items():
            # Filter by test name
            mmrs_test = mmrs_run.filter(result__iteration__test__name=test_name)
            mmrs_test_ids = list(mmrs_test.values_list('id', flat=True))
            if not mmrs_test_ids:
                continue

            # Filter by axis_y
            axis_y = test_config['axis_y']
            mmrs_test_ids = filter_by_axis_y(mmrs_test, axis_y)
            if not mmrs_test_ids:
                msg = (
                    f'{test_name} test: no results after filtering by axis_y value. '
                    'Fix report configuration'
                )
                warnings.append(msg)
                continue
            mmrs_test = mmrs_run.filter(id__in=mmrs_test_ids)

            # Filter by not_show_args
            not_show_args = test_config['not_show_args']
            mmrs_test_ids = filter_by_not_show_args(mmrs_test, not_show_args)
            if not mmrs_test_ids:
                msg = (
                    f'{test_name} test: no results after filtering by not_show_args value. '
                    'Fix report configuration'
                )
                warnings.append(msg)
                test_names_order = report_config.get('test_names_order')
                if test_names_order and test_name in test_names_order:
                    report_config['test_names_order'].remove(test_name)
                continue

            # Collect common args
            common_args[test_name] = get_common_args(mmrs_test_ids)

            report_q |= Q(id__in=mmrs_test_ids)

        mmrs_report = (
            mmrs_run.filter(report_q).order_by('id')
            if report_q
            else MeasurementResult.objects.none()
        )

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
        if report_config.get('test_names_order'):
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
