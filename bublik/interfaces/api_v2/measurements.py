# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import contextlib

from itertools import groupby
import typing

from django.conf import settings
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.measurement.representation import ChartViewBuilder
from bublik.core.measurement.services import (
    get_chart_views,
    get_points_chart_views,
    get_y_chart_views,
    represent_measurements,
)
from bublik.data.models import Measurement
from bublik.data.serializers import MeasurementSerializer


all = [
    'MeasurementViewSet',
]


class MeasurementViewSet(GenericViewSet):
    queryset = Measurement.objects.all()
    serializer_class = MeasurementSerializer
    search_fields: typing.ClassVar['str'] = ['tool', 'type', 'name', 'keys', 'aggr']

    def list(self, request):
        serializer = self.get_serializer(Measurement.objects.filter(), many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post', 'get'])
    def by_result_ids(self, request):
        result_ids = None
        if request.method == 'GET':
            with contextlib.suppress(AttributeError):
                result_ids = request.query_params.get('result_ids').split(
                    settings.QUERY_DELIMITER,
                )
        elif request.method == 'POST':
            result_ids = request.data.get('result_ids')
        if not result_ids:
            return Response({'error': 'No results ids specified'})

        chart_views = get_chart_views(result_ids)

        if chart_views:
            chart_views_lines = get_y_chart_views(chart_views)
            chart_views_points = get_points_chart_views(chart_views)

            # Get charts from processing line graph chart views
            charts_from_lines = []
            for line_chart_view in chart_views_lines:
                chart = ChartViewBuilder.by_line_graph(line_chart_view)
                charts_from_lines.append(chart.representation())

            # Get charts from processing point chart views
            # Need to group chart_views_points by measurements to make line-graphs from them
            def grouper_by_measurement(cv):
                return cv.measurement.id

            charts_from_points = []
            chart_views_sorted = sorted(chart_views_points, key=grouper_by_measurement)
            for _measurement_id, points_chart_views in groupby(
                chart_views_sorted,
                key=grouper_by_measurement,
            ):
                chart = ChartViewBuilder.by_points(points_chart_views)
                charts_from_points.append(chart)
                # Make ChartViewBuilder process point chart view as well:
                # - init by CV line object | list of CV point objects ->
                # -> ChartViewBuilder.by_points(cvs)

            data = charts_from_lines + charts_from_points
        else:
            data = represent_measurements(result_ids)

        return Response(data)

    def retrieve(self, request, pk=None):
        measurement = self.get_object()
        serializer = self.get_serializer(measurement)
        return Response(serializer.data)
