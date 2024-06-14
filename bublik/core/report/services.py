# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib

from itertools import groupby
import json
import os

from bublik import settings
from bublik.data.models import MeasurementResult, Meta, TestArgument
from bublik.settings import REPORT_CONFIG_COMPONENTS


def get_report_config():
    report_configs_dir = os.path.join(settings.PER_CONF_DIR, 'reports')
    for root, _, report_config_files in os.walk(report_configs_dir):
        for report_config_file in report_config_files:
            with open(os.path.join(root, report_config_file)) as rсf:
                try:
                    report_config = json.load(rсf)
                    yield report_config_file, report_config
                except json.decoder.JSONDecodeError:
                    yield report_config_file, None


def get_report_config_by_id(report_config_id):
    for _, report_config in get_report_config():
        if report_config:
            try:
                if str(report_config['id']) == report_config_id:
                    return report_config
            except KeyError:
                continue
    return None


def check_report_config(report_config):
    '''
    Check the passed report config for compliance with the expected format.
    '''
    for key in REPORT_CONFIG_COMPONENTS['required_keys']:
        if key not in report_config.keys():
            msg = f'the required key \'{key}\' is missing in the configuration'
            raise KeyError(msg)

    for test_name, test_configuration in report_config['tests'].items():
        for key in REPORT_CONFIG_COMPONENTS['required_test_keys']:
            if key not in test_configuration.keys():
                msg = (
                    f'the required key \'{key}\' is missing for \'{test_name}\' '
                    'test in the configuration'
                )
                raise KeyError(msg)


def build_report_title(main_pkg, title_content):
    '''
    Form the title of the report according to configuration.
    '''
    meta_labels = Meta.objects.filter(
        metaresult__result__id=main_pkg.id,
        type='label',
        name__in=title_content,
    ).values_list('name', 'value')
    meta_labels = dict(meta_labels)

    title = []
    for title_obj in title_content:
        if title_obj in meta_labels:
            title.append(meta_labels[title_obj])
    return '-'.join(title)


def type_conversion(arg_value):
    with contextlib.suppress(AttributeError):
        if arg_value.isdigit():
            return int(arg_value)
    return arg_value


def sequence_name_conversion(seq_arg_val, test_config):
    '''
    Convert the passed sequence name according to the passed test configuration.
    '''
    seq_arg_val = str(seq_arg_val)
    sequence_name_conversion = test_config['sequence_name_conversion']
    with contextlib.suppress(KeyError):
        return str(sequence_name_conversion[seq_arg_val])
    return seq_arg_val


def args_type_convesion(point_groups_by_test_name):
    '''
    The argument values are used to sort the records. Thus, for proper sorting, it is necessary
    to determine numeric arguments and convert their values from str to int.
    '''
    args_to_convert = {}
    for test_name, test_points in point_groups_by_test_name.items():
        args_to_convert[test_name] = set(test_points[0].args_vals.keys())
        for test_point in test_points:
            for arg, val in test_point.args_vals.items():
                if not isinstance(type_conversion(val), int):
                    args_to_convert[test_name].discard(arg)

    for test_name, test_points in point_groups_by_test_name.items():
        for test_point in test_points:
            for arg in args_to_convert[test_name]:
                test_point.args_vals[arg] = type_conversion(test_point.args_vals[arg])

    return point_groups_by_test_name


def args_sort(records_order, args_vals):
    if records_order:
        return dict([arg, args_vals[arg]] for arg in records_order)
    return dict(sorted(args_vals.items()))


def get_common_args(main_pkg, test_name):
    '''
    Collect arguments that have the same values for all iterations of the test
    with the passed name within the passed package.
    '''
    common_args = {}
    test_args = (
        TestArgument.objects.filter(
            test_iterations__testiterationresult__test_run=main_pkg,
            test_iterations__test__name=test_name,
        )
        .order_by('name', 'value')
        .distinct('name', 'value')
        .values('name', 'value')
    )

    for arg, arg_val in groupby(test_args, key=lambda x: x['name']):
        arg_val = list(arg_val)
        if len(arg_val) == 1:
            common_args[arg] = type_conversion(arg_val[0]['value'])

    return common_args


def filter_by_axis_y(mmrs_test, axis_y):
    '''
    Filter passed measurement results QS by axis y value from config.
    '''
    for meas_type, meas_names in axis_y.items():
        mmrs_test = mmrs_test.filter(
            measurement__metas__name='type',
            measurement__metas__type='measurement_subject',
            measurement__metas__value=meas_type,
        )
        if meas_names:
            mmrs_test = mmrs_test.filter(
                measurement__metas__name='name',
                measurement__metas__type='measurement_subject',
                measurement__metas__value__in=meas_names,
            )

    return mmrs_test


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
