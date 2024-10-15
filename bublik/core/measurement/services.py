# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from bublik.data.models import (
    ChartView,
    ChartViewType,
    Measurement,
    MeasurementResult,
    MeasurementResultList,
    View,
)


def get_measurement_results(result_ids, measurement=None):
    # TODO type processing
    measurement_results = (
        MeasurementResult.objects.filter(result__id__in=result_ids)
        .order_by('measurement__id')
        .select_related(
            'measurement',
            'result',
            'result__test_run',
            'result__iteration',
            'result__iteration__test',
        )
    )
    if measurement:
        return measurement_results.filter(measurement=measurement)
    return measurement_results


def get_measurement_result_lists(result_id, measurement=None):
    measurement_result_lists = MeasurementResultList.objects.filter(
        result__id=result_id,
    )
    if measurement:
        return measurement_result_lists.filter(measurement=measurement).first()
    return measurement_result_lists


def get_measurements(result_ids):
    return Measurement.objects.filter(measurement_results__result__id__in=result_ids).distinct(
        'id',
    )


def get_measurement_results_or_none(result_id):
    data = get_measurement_results([result_id])
    if not data:
        return None
    measurement_results = []
    for d in data:
        measurement_results.append(d.id)

    return measurement_results


def exist_measurement_results(test_result):
    return test_result.measurement_results.exists()


def get_views(result_id):
    return View.objects.filter(chart_views__result_id=result_id).distinct('id')


def get_line_graph_views(views):
    return views.filter(metas__value='line-graph')


def get_point_views(views):
    return views.filter(metas__value='point')


def get_chart_views(result_id, view):
    return (
        ChartView.objects.filter(result_id=result_id, view=view)
        .select_related(
            'view',
            'measurement',
        )
        .distinct('id')
    )


def get_x_chart_view(line_chart_views):
    return line_chart_views.get(
        type=ChartViewType.conv(ChartViewType.AXIS_X),
    )


def get_y_chart_views(line_chart_views):
    return line_chart_views.filter(
        type=ChartViewType.conv(ChartViewType.AXIS_Y),
    )
