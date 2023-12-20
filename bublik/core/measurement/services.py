# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import groupby
import logging

from bublik.data.models import (
    ChartView,
    ChartViewType,
    MeasurementResult,
)


logger = logging.getLogger('bublik.server')


def get_measurements_by_result(result_id):
    # TODO type processing
    return MeasurementResult.objects.filter(result__id=result_id).select_related(
        'measurement',
        'result',
        'result__iteration',
        'result__iteration__test',
    )


def get_measurement_results(iteration_results, measurement):
    return MeasurementResult.objects.filter(
        result__in=iteration_results,
        measurement=measurement,
    ).select_related('result', 'result__iteration')


def get_measurement_results_or_none(result_id):
    data = get_measurements_by_result(result_id)
    if not data:
        return None
    measurement_results = []
    for d in data:
        measurement_results.append(d.id)

    return measurement_results


def exist_measurement_results(test_result):
    return test_result.measurement_results.exists()


def get_chart_views(results_ids):
    return ChartView.objects.filter(result_id__in=results_ids).select_related(
        'view',
        'measurement',
    )


def merge_measurements(data):
    # The results of measurements that differ from each other only
    # by the value of comments should be analyzed together,
    # since comments is not the key information.
    # Thus, the charts corresponding to such measurements
    # need to be merged.
    final_data = []
    data_num = len(data)
    for _num in range(data_num):
        if len(data) > 0:
            final_d = data.pop(0)
            for dot in final_d['dots']:
                dot['comments'] = final_d['comments']

            data_copy = data.copy()
            for d in data_copy:
                if all(
                    final_d[k] == d[k]
                    for k in final_d
                    if k not in ('measurement_id', 'comments', 'dots')
                ):
                    for dot in d['dots']:
                        dot['comments'] = d['comments']
                    final_d['dots'].extend(d['dots'])
                    data.remove(d)

            final_d.pop('measurement_id')
            final_d.pop('comments')
            final_d['dots'].sort(key=lambda dot: dot['start'])

            final_data.append(final_d)
        else:
            break

    # sort so that the charts containing the newest results
    # are shown first
    final_data.sort(key=lambda measurement: measurement['dots'][-1]['start'], reverse=True)

    return final_data


def represent_measurements(results_ids):
    logger.debug('[represent_measurements]: start for %d ids', len(results_ids))
    mmrs = list(
        MeasurementResult.objects.filter(result__in=results_ids).order_by('measurement__id')
        # we need all the related fields because final json gives information
        # about result and run so that UI is able to provide convenient links to
        # the user
        .select_related('measurement', 'result', 'result__test_run', 'result__iteration'),
    )
    logger.debug(
        '[represent_measurements]: collected %d measurement results from DB',
        len(mmrs),
    )

    def measurements_grouper(mmr):
        return mmr.measurement.id

    data = []
    logger.debug(
        '[represent_measurements]: Number of measurement groups: %d',
        len(list(groupby(mmrs, key=measurements_grouper))),
    )
    for _measurement_id, mmrs_groups in groupby(mmrs, key=measurements_grouper):
        mmrs_groups_list = list(mmrs_groups)
        measurement = mmrs_groups_list[0].measurement

        mm_data = measurement.representation()
        mm_data['dots'] = [
            mmr.representation(mm_data['multiplier']) for mmr in mmrs_groups_list
        ]

        title_items = []
        if mm_data['name']:
            title_items.append(mm_data['name'].capitalize())
        else:
            title_items.append(mm_data['tool'].capitalize())
        if mm_data['aggr']:
            title_items.append(mm_data['aggr'].capitalize())
        if mm_data['keys']:
            title_items.append(', '.join(mm_data['keys']))

        config = {
            'title': ' - '.join(title_items),
            'default_x': 'start',
            'default_y': 'value',
            'getters': ['start', 'value', 'sequence_number'],
            'axises': {
                'start': {
                    'getter': 'start',
                    'label': 'Start of measurement test',
                    'units': 'timestamp',
                },
                'sequence_number': {
                    'getter': 'sequence_number',
                    'label': 'Sequence Number',
                    'units': '',
                },
                'value': {
                    'getter': 'value',
                    'label': mm_data['type'],
                    'units': 'units',
                },
            },
        }
        mm_data['axises_config'] = config

        data.append(mm_data)

    if len(results_ids) > 1:
        data = merge_measurements(data)
    logger.debug('[represent_measurements]: completed, data len = %d', len(data))

    return data


def get_lines_chart_views(chart_views):
    # Get line-graph chart views where test result has number of measurements.
    # Since it's going to change ChartView type options to point/line,
    # now filtering 'type = axis_y' is added
    return chart_views.filter(
        view__metas__value='line-graph',
        type=ChartViewType.conv(ChartViewType.AXIS_Y),
    )


def get_points_chart_views(chart_views):
    # Get point chart views where result has one measurement
    # and build line-graph thought different runs with the same measurement
    return chart_views.filter(
        view__metas__value='point',
        type=ChartViewType.conv(ChartViewType.POINT),
    )
