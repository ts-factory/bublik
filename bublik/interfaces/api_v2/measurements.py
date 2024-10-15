# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import contextlib
import typing

from django.conf import settings
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.measurement.representation import ChartViewBuilder
from bublik.core.measurement.services import get_measurements
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

        measurements = get_measurements(result_ids)
        charts = [
            ChartViewBuilder(measurement).by_measurement_results(result_ids)
            for measurement in measurements
        ]
        # The results of measurements that differ from each other only
        # by the value of comments should be analyzed together,
        # since comments is not the key information.
        # Thus, the charts corresponding to such measurements
        # need to be merged.
        merge_mm_key = [
            key
            for key in next(iter(measurements)).representation()
            if key not in ['measurement_id', 'comments']
        ]
        merged_charts = ChartViewBuilder.merge_charts_by(charts, merge_mm_key)

        return Response([merged_chart.representation() for merged_chart in merged_charts])

    def retrieve(self, request, pk=None):
        measurement = self.get_object()
        serializer = self.get_serializer(measurement)
        return Response(serializer.data)
