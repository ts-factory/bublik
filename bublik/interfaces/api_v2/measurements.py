# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.measurement.representation import ChartViewBuilder
from bublik.core.measurement.services import get_measurement_results
from bublik.core.utils import unordered_group_by
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

    @action(detail=False, methods=['post'])
    def by_result_ids(self, request):
        result_ids = request.data.get('result_ids', None)
        if not result_ids:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValueError', 'message': 'No result IDs specified'},
            )

        mmrs = get_measurement_results(result_ids)
        mmrs_groups = unordered_group_by(mmrs, 'measurement_group_key')

        return Response(
            [
                (
                    ChartViewBuilder(next(iter(mmr_group)).measurement).by_measurement_results(
                        mmr_group,
                    )
                ).representation()
                for _mm_key, mmr_group in mmrs_groups.items()
            ],
        )

    def retrieve(self, request, pk=None):
        measurement = self.get_object()
        serializer = self.get_serializer(measurement)
        return Response(serializer.data)
