# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict
import typing

from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.measurement.representation import ChartViewBuilder
from bublik.core.measurement.services import get_measurement_charts, get_measurement_results
from bublik.core.utils import key_value_dict_transforming, unordered_group_by
from bublik.data.models import Measurement, TestIterationResult
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
    def trend_charts(self, request):
        result_ids = request.data.get('result_ids', None)
        if not result_ids:
            msg = 'No result IDs specified'
            raise ValidationError(msg)

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

    @action(detail=False, methods=['post'])
    def by_result_ids(self, request):
        result_ids = request.data.get('result_ids', None)
        if not result_ids:
            msg = 'No result IDs specified'
            raise ValidationError(msg)

        measurement_series_charts_by_result = []
        for result_id in result_ids:
            test_result = TestIterationResult.objects.get(id=result_id)

            # get parameters
            parameters = {}
            for test_argument in test_result.iteration.test_arguments.all():
                parameters[test_argument.name] = test_argument.value
            parameters = OrderedDict(sorted(parameters.items()))
            parameters_list = key_value_dict_transforming(parameters)

            # get chart data
            measurement_series_charts = []
            for chart in get_measurement_charts(result_id):
                chart['id'] = f'{test_result.id}_{chart["id"]}'
                measurement_series_charts.append(chart)

            measurement_series_charts_by_result.append(
                {
                    'run_id': test_result.test_run_id,
                    'result_id': test_result.id,
                    'start': test_result.start,
                    'test_name': test_result.iteration.test.name,
                    'parameters_list': parameters_list,
                    'measurement_series_charts': measurement_series_charts,
                },
            )

        return Response(measurement_series_charts_by_result)

    def retrieve(self, request, pk=None):
        measurement = self.get_object()
        serializer = self.get_serializer(measurement)
        return Response(serializer.data)
