# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class DashboardRunContextSerializer(serializers.Serializer):
    run_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    start = serializers.FloatField()
    status = serializers.CharField(allow_null=True)
    status_by_nok = serializers.CharField()
    conclusion = serializers.CharField()
    conclusion_reason = serializers.CharField(allow_null=True)


class DashboardRowSerializer(serializers.Serializer):
    row_cells = serializers.DictField(child=serializers.JSONField(allow_null=True))
    context = DashboardRunContextSerializer()


class DashboardColumnSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()


class DashboardResponseSerializer(serializers.Serializer):
    date = serializers.CharField()
    rows = DashboardRowSerializer(many=True)
    header = DashboardColumnSerializer(many=True)
    payload = serializers.DictField(child=serializers.CharField())
