# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib

from itertools import groupby
import json
import os

from django.conf import settings

from bublik.core.utils import get_metric_prefix_units
from bublik.data.models import MeasurementResult, Meta, TestArgument


def get_report_config():
    report_configs_dir = os.path.join(settings.PER_CONF_DIR, 'reports')
    for root, _, report_config_files in os.walk(report_configs_dir):
        for report_config_file in report_config_files:
            with open(os.path.join(root, report_config_file)) as rcf:
                try:
                    report_config = json.load(rcf)
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
    try:
        report_config_components = settings.REPORT_CONFIG_COMPONENTS
    except AttributeError as ae:
        msg = (
            "The key 'REPORT_CONFIG_COMPONENTS' is missing in the settings. "
            "Take the 'django_settings' deployment step."
        )
        raise AttributeError(
            msg,
        ) from ae

    for key in report_config_components['required_keys']:
        if key not in report_config.keys():
            msg = f'the required key \'{key}\' is missing in the configuration'
            raise KeyError(msg)

    possible_axis_y_keys = report_config_components['possible_axis_y_keys']
    for test_name, test_configuration in report_config['tests'].items():
        for key in report_config_components['required_test_keys']:
            if key not in test_configuration.keys():
                msg = (
                    f'the required key \'{key}\' is missing for \'{test_name}\' '
                    'test in the configuration'
                )
                raise KeyError(msg)
        for measurement in test_configuration['axis_y']:
            for meas_param in measurement:
                if meas_param not in possible_axis_y_keys:
                    payk_str = ', '.join(f'\'{key}\'' for key in possible_axis_y_keys)
                    msg = (
                        f'unsupported measurement parameter \'{meas_param}\' for '
                        f'\'{test_name}\' test in the configuration. Possible are {payk_str}'
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


def build_axis_y_name(mmr):
    '''
    Form the y axis name according to the scheme:
    "<measurement name>/<measurement type> - <aggr> -
    <measurement key name>:<measurement key value> (<base_units> * <multiplier>)".
    '''
    # get metas
    meta_subjects = {}
    meta_keys = {}
    for meta in mmr.measurement.metas.filter(
        type='measurement_subject',
        name__in=['name', 'type', 'aggr', 'base_units', 'multiplier'],
    ):
        meta_subjects[meta.name] = meta.value
    for meta in mmr.measurement.metas.filter(type='measurement_key'):
        meta_keys[meta.name] = meta.value

    # build units part
    axis_y_tail = ''
    if meta_subjects['base_units'] and meta_subjects['multiplier']:
        try:
            axis_y_tail = get_metric_prefix_units(
                meta_subjects['multiplier'],
                meta_subjects['base_units'],
            )
        except KeyError:
            base_units = meta_subjects['base_units']
            multiplier = meta_subjects['multiplier']
            axis_y_tail = f'{base_units} * {multiplier}'
    meta_subjects.pop('base_units')
    meta_subjects.pop('multiplier')

    # build main part
    axis_y_items = []
    if 'name' in meta_subjects:
        axis_y_items.append(meta_subjects['name'])
        meta_subjects.pop('name')
        meta_subjects.pop('type')
    else:
        axis_y_items.append(meta_subjects['type'])
        meta_subjects.pop('type')
    for _, value in meta_subjects.items():
        axis_y_items.append(value)
    for name, value in meta_keys.items():
        axis_y_items.append(f'{name}={value}')
    axis_y_name = ' - '.join(axis_y_items)

    # join parts
    if axis_y_tail:
        axis_y_name = f'{axis_y_name} ({axis_y_tail})'

    return axis_y_name


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
        args_vals_sorted = {arg: args_vals[arg] for arg in records_order if arg in args_vals}
        args_vals_other = {
            arg: val for arg, val in args_vals.items() if arg not in args_vals_sorted
        }
        return args_vals_sorted | args_vals_other
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
