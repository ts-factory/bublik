# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib
from itertools import groupby

from bublik.data.models import MeasurementResult, TestArgument


def type_conversion(arg_value):
    with contextlib.suppress(AttributeError):
        if arg_value.isdigit():
            return int(arg_value)
    return arg_value


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
