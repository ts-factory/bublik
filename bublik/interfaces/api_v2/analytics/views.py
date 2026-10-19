# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.viewsets import GenericViewSet

from bublik.analytics.serializers import (
    AnalyticsCollectRequestSerializer,
    AnalyticsEventSerializer,
)
from bublik.core.analytics.services import AnalyticsService
from bublik.core.auth import auth_required


__all__ = ['AnalyticsViewSet']


class AnalyticsCollectThrottle(SimpleRateThrottle):
    scope = 'analytics_collect'
    rate = getattr(settings, 'ANALYTICS_COLLECT_THROTTLE_RATE', '120/min')

    def get_cache_key(self, request, view):
        return self.cache_format % {
            'scope': self.scope,
            'ident': self.get_ident(request),
        }


class AnalyticsViewSet(ListModelMixin, GenericViewSet):
    serializer_class = AnalyticsEventSerializer
    filter_backends: typing.ClassVar[list] = []

    def get_queryset(self):
        return AnalyticsService.get_filtered_queryset(self.request.query_params)

    @auth_required(as_admin=True)
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @action(detail=False, methods=['post'], throttle_classes=[AnalyticsCollectThrottle])
    def collect(self, request):
        serializer = AnalyticsCollectRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        validated_data = typing.cast('dict[str, typing.Any]', serializer.validated_data)

        result = {'received': AnalyticsService.collect_events(validated_data['events'])}

        return Response(
            data=result,
            status=status.HTTP_202_ACCEPTED,
        )

    @auth_required(as_admin=True)
    @action(detail=False, methods=['get'])
    def overview(self, request):
        queryset = self.get_queryset()
        return Response(data=AnalyticsService.get_overview(queryset))

    @auth_required(as_admin=True)
    @action(detail=False, methods=['get'])
    def facets(self, request):
        queryset = self.get_queryset()
        return Response(data=AnalyticsService.get_facets(queryset))

    @auth_required(as_admin=True)
    @action(detail=False, methods=['get'])
    def charts(self, request):
        queryset = self.get_queryset()
        return Response(
            data=AnalyticsService.get_charts_for_query(queryset, request.query_params),
        )
