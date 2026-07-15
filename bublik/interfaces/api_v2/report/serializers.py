# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class RunReportConfigSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    version = serializers.IntegerField()
    project = serializers.IntegerField(allow_null=True)
    description = serializers.CharField(allow_blank=True)


class ReportConfigListResponseSerializer(serializers.Serializer):
    run_report_configs = RunReportConfigSerializer(many=True)


class ReportRetrieveQuerySerializer(serializers.Serializer):
    config = serializers.IntegerField(required=True)


class ReportConfigSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    version = serializers.IntegerField()


class ReportUnprocessedItersSerializer(serializers.Serializer):
    test_name = serializers.CharField()
    common_args = serializers.DictField()
    args_vals = serializers.DictField()
    reasons = serializers.ListField(
        child=serializers.CharField(),
    )


class ReportAxisSerializer(serializers.Serializer):
    label = serializers.CharField()
    key = serializers.CharField()
    values = serializers.ListField(
        child=serializers.JSONField(),
        required=False,
    )


class ReportPointMetadataSerializer(serializers.Serializer):
    iteration_id = serializers.IntegerField()
    result_id = serializers.IntegerField()
    has_error = serializers.BooleanField()


class ReportPointSerializer(serializers.Serializer):
    x_value = serializers.JSONField()
    y_value = serializers.JSONField(allow_null=True)
    metadata = ReportPointMetadataSerializer(required=False)


class ReportRecordDataSerializer(serializers.Serializer):
    series = serializers.CharField(
        allow_blank=True,
        required=False,
    )
    points = ReportPointSerializer(many=True)


class ReportChartSerializer(serializers.Serializer):
    warnings = serializers.ListField(
        child=serializers.CharField(),
    )
    axis_x = ReportAxisSerializer()
    axis_y = ReportAxisSerializer()
    series_label = serializers.CharField(
        allow_blank=True,
        required=False,
    )
    data = ReportRecordDataSerializer(many=True)


class ReportTableSerializer(serializers.Serializer):
    warnings = serializers.ListField(
        child=serializers.CharField(),
    )
    formatters = serializers.DictField(
        child=serializers.CharField(),
        required=False,
    )
    labels = serializers.DictField(
        child=serializers.CharField(),
    )
    data = ReportRecordDataSerializer(many=True)


class ReportRecordContentSerializer(serializers.Serializer):
    type = serializers.CharField()
    id = serializers.CharField()
    label = serializers.CharField()
    chart = ReportChartSerializer(required=False)
    table = ReportTableSerializer(required=False)


class ReportMeasurementContentSerializer(serializers.Serializer):
    type = serializers.CharField()
    label = serializers.CharField()
    id = serializers.CharField()
    content = ReportRecordContentSerializer(many=True)


class ReportArgsValsContentSerializer(serializers.Serializer):
    type = serializers.CharField()
    args_vals = serializers.DictField(
        child=serializers.JSONField(),
    )
    label = serializers.CharField()
    id = serializers.CharField()
    content = ReportMeasurementContentSerializer(many=True)


class ReportTestContentSerializer(serializers.Serializer):
    type = serializers.CharField()
    id = serializers.CharField()
    label = serializers.CharField()
    enable_table_view = serializers.BooleanField()
    enable_chart_view = serializers.BooleanField()
    common_args = serializers.DictField(
        child=serializers.JSONField(),
    )
    content = ReportArgsValsContentSerializer(many=True)


class ReportRetrieveResponseSerializer(serializers.Serializer):
    warnings = serializers.ListField(
        child=serializers.CharField(),
    )
    config = ReportConfigSerializer()
    content = ReportTestContentSerializer(many=True)
    unprocessed_iters = ReportUnprocessedItersSerializer(many=True)
