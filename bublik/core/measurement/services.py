# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import groupby

from django.core.exceptions import ObjectDoesNotExist

from bublik.core.measurement.representation import ChartViewBuilder
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


def get_measurement_charts(result_id):
    views = get_views(result_id)
    try:
        # check if there are any views
        if not views:
            raise ObjectDoesNotExist
        # get line-graph charts
        charts_from_lines = []
        line_graph_views = get_line_graph_views(views)
        if line_graph_views:
            for view in line_graph_views:
                line_chart_views = get_chart_views(result_id, view)
                x_chart_view = get_x_chart_view(line_chart_views)
                y_chart_views = get_y_chart_views(line_chart_views)

                axis_x = get_measurement_result_lists(
                    x_chart_view.result.id,
                    x_chart_view.measurement,
                )

                for y_chart_view in y_chart_views:
                    axis_y = get_measurement_result_lists(
                        y_chart_view.result.id,
                        y_chart_view.measurement,
                    )
                    charts_from_lines.append(
                        ChartViewBuilder(axis_y.measurement, y_chart_view.view)
                        .by_lines(
                            axis_y,
                            axis_x,
                        )
                        .representation(),
                    )
        # get point charts
        charts_from_points = []
        point_views = get_point_views(views)
        if point_views:

            def grouper_by_measurement(cv):
                return cv.measurement.id

            for view in point_views:
                point_chart_views = get_chart_views(result_id, view)
                point_chart_views = sorted(point_chart_views, key=grouper_by_measurement)
                for _measurement_id, points in groupby(
                    point_chart_views,
                    key=grouper_by_measurement,
                ):
                    charts_from_points.append(ChartViewBuilder.by_points(points))
                    # Make ChartViewBuilder process point chart view as well:
                    # - init by CV line object | list of CV point objects ->
                    # -> ChartViewBuilder.by_points(cvs)
        # get all views charts
        charts = charts_from_lines + charts_from_points
    except ObjectDoesNotExist:
        mmr_lists = get_measurement_result_lists(result_id)
        charts = [
            ChartViewBuilder(mmr_list.measurement).by_lines(mmr_list).representation()
            for mmr_list in mmr_lists
        ]
    return charts
