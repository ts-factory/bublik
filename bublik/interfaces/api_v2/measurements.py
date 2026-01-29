# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
from __future__ import annotations

import typing

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.measurement.services import MeasurementService
from bublik.data.models import Measurement
from bublik.data.serializers import MeasurementSerializer


all = [
    'MeasurementViewSet',
]


class MeasurementViewSet(GenericViewSet):
    queryset = Measurement.objects.all()
    serializer_class = MeasurementSerializer
    search_fields: typing.ClassVar[list[str]] = ['tool', 'type', 'name', 'keys', 'aggr']

    def list(self, request):
        queryset = MeasurementService.list_measurements()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def trend_charts(self, request):
        result_ids = request.data.get('result_ids', None)
        if not result_ids:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValueError', 'message': 'No result IDs specified'},
            )

        charts = MeasurementService.get_trend_charts(result_ids)
        return Response(charts)

    @action(detail=False, methods=['post'])
    def by_result_ids(self, request):
        result_ids = request.data.get('result_ids', None)
        if not result_ids:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValueError', 'message': 'No result IDs specified'},
            )

        measurements = MeasurementService.get_measurements_by_result_ids(result_ids)
        return Response(measurements)

    def retrieve(self, request, pk=None):
        measurement = MeasurementService.get_measurement(pk)
        serializer = self.get_serializer(measurement)
        return Response(serializer.data)
