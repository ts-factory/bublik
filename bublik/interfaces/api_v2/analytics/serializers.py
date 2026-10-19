# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class AnalyticsValueCountSerializer(serializers.Serializer):
    value = serializers.CharField()
    count = serializers.IntegerField()


class AnalyticsEventNameCountSerializer(serializers.Serializer):
    event_name = serializers.CharField(source='value')
    count = serializers.IntegerField()


class AnalyticsPathCountSerializer(serializers.Serializer):
    path = serializers.CharField(source='value')
    count = serializers.IntegerField()


class AnalyticsOverviewResponseSerializer(serializers.Serializer):
    total_events = serializers.IntegerField()
    page_views = serializers.IntegerField()
    unique_anonymous_users = serializers.IntegerField()
    top_event_names = AnalyticsEventNameCountSerializer(many=True)
    top_paths = AnalyticsPathCountSerializer(many=True)


class AnalyticsFacetsResponseSerializer(serializers.Serializer):
    event_types = AnalyticsValueCountSerializer(many=True)
    event_names = AnalyticsValueCountSerializer(many=True)
    paths = AnalyticsValueCountSerializer(many=True)
    anon_ids = AnalyticsValueCountSerializer(many=True)
    app_versions = AnalyticsValueCountSerializer(many=True)


class AnalyticsChartsResponseSerializer(serializers.Serializer):
    top_events_path = serializers.CharField(allow_blank=True)
    top_paths = AnalyticsPathCountSerializer(many=True)
    top_events = AnalyticsEventNameCountSerializer(many=True)
