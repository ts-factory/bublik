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


class ReportConfigSerializer(serializers.Serializer):
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    version = serializers.IntegerField()


class ReportUnprocessedItersSerializer(serializers.Serializer):
    test_name = serializers.CharField()
    common_args = serializers.DictField()
    args_vals = serializers.DictField()
    reasons = serializers.ListField(child=serializers.CharField())


class ReportRetrieveResponseSerializer(serializers.Serializer):
    warnings = serializers.ListField(child=serializers.CharField())
    config = ReportConfigSerializer()
    content = serializers.ListField(child=serializers.DictField())
    unprocessed_iters = ReportUnprocessedItersSerializer(many=True)
