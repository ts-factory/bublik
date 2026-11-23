# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers


class DashboardProjectQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(
        required=False,
        help_text='Project ID. Omit to use global dashboard settings.',
    )


class DashboardListQuerySerializer(DashboardProjectQuerySerializer):
    date = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text='Dashboard date (usually YYYY-MM-DD). Defaults to the latest available date.',
    )


class DashboardRunContextSerializer(serializers.Serializer):
    run_id = serializers.IntegerField()
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    start = serializers.FloatField(help_text='Run start time as a Unix timestamp in seconds.')
    status = serializers.CharField(allow_null=True)
    status_by_nok = serializers.CharField()
    conclusion = serializers.CharField()
    conclusion_reason = serializers.CharField(allow_null=True)


class DashboardRowSerializer(serializers.Serializer):
    row_cells = serializers.DictField(
        child=serializers.JSONField(allow_null=True),
        help_text='Cell values keyed by configured column name.',
    )
    context = DashboardRunContextSerializer()


class DashboardColumnSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()


@extend_schema_serializer(many=False)
class DashboardResponseSerializer(serializers.Serializer):
    date = serializers.CharField()
    rows = DashboardRowSerializer(many=True)
    header = DashboardColumnSerializer(many=True)
    payload = serializers.DictField(
        child=serializers.CharField(),
        help_text='Navigation handler descriptions keyed by column name.',
    )


class DashboardModeSerializer(serializers.Serializer):
    days = serializers.IntegerField()
    columns = serializers.IntegerField()


class DashboardDefaultModeResponseSerializer(serializers.Serializer):
    mode = DashboardModeSerializer()
