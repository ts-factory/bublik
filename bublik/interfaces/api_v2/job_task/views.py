# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

import django_filters
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.job_task import JobTaskExecutionService
from bublik.data.models import (
    JobTaskExecutionResult,
    TaskExecution,
)
from bublik.interfaces.api_v2.job_task.schemas import job_task_execution_viewset_schema
from bublik.interfaces.api_v2.job_task.serializers import (
    JobTaskExecutionListSerializer,
    JobTaskExecutionRetrieveSerializer,
)


__all__ = ['JobTaskExecutionViewSet']


class JobTaskExecutionFilterSet(filters.FilterSet):
    job = django_filters.NumberFilter(field_name='job_id')
    run = django_filters.NumberFilter(field_name='run_id')
    url = django_filters.CharFilter(field_name='url')
    celery_task = django_filters.UUIDFilter(field_name='task_execution__task_id')
    status = django_filters.ChoiceFilter(
        field_name='task_execution__status',
        choices=TaskExecution.StatusChoices.choices,
    )

    class Meta:
        model = JobTaskExecutionResult
        fields: typing.ClassVar = []


@job_task_execution_viewset_schema
class JobTaskExecutionViewSet(ListModelMixin, GenericViewSet):
    queryset = JobTaskExecutionService.list_job_task_queryset()
    serializer_class = JobTaskExecutionListSerializer
    filterset_class = JobTaskExecutionFilterSet
    filter_backends: typing.ClassVar[list] = [DjangoFilterBackend]

    def retrieve(self, request, pk=None):
        task_results = JobTaskExecutionService.get_tasks_by_job(job_id=pk)
        serializer = JobTaskExecutionRetrieveSerializer(task_results, many=True)
        return Response(serializer.data)

    def list(self, request, *args, **kwargs):
        filtered_qs = self.filter_queryset(self.get_queryset())
        task_results = JobTaskExecutionService.list_tasks(filtered_qs)

        page = self.paginate_queryset(task_results)
        serializer = self.get_serializer(
            page if page is not None else task_results,
            many=True,
        )

        if page is not None:
            return self.get_paginated_response(serializer.data)

        return Response(serializer.data)
