# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class MeasurementChartSerializer(serializers.Serializer):
    id = serializers.JSONField()
    title = serializers.CharField(allow_null=True)
    subtitle = serializers.CharField()
    axis_x = serializers.DictField()
    axis_y = serializers.DictField()
    dataset = serializers.ListField(child=serializers.ListField(child=serializers.JSONField()))


class MeasurementByResultSerializer(serializers.Serializer):
    run_id = serializers.IntegerField()
    result_id = serializers.IntegerField()
    start = serializers.DateTimeField()
    test_name = serializers.CharField()
    parameters_list = serializers.ListField(child=serializers.CharField())
    measurement_series_charts = MeasurementChartSerializer(many=True)


class MeasurementMetasSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()
    value = serializers.CharField()
    comment = serializers.CharField(allow_null=True)


class MeasurementListResponseSerializer(serializers.Serializer):
    metas = MeasurementMetasSerializer(many=True)


class MeasurementRequestBodySerializer(serializers.Serializer):
    result_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
    )
