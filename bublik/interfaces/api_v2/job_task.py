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


class JobTaskExecutionViewSet(ListModelMixin, GenericViewSet):
    queryset = JobTaskExecutionService.list_job_task_queryset()
    filterset_class = JobTaskExecutionFilterSet
    filter_backends: typing.ClassVar[list] = [DjangoFilterBackend]

    def retrieve(self, request, pk=None):
        return Response(JobTaskExecutionService.get_tasks_by_job(job_id=pk))

    def list(self, request, *args, **kwargs):
        filtered_qs = self.filter_queryset(self.get_queryset())
        task_results = JobTaskExecutionService.list_tasks(filtered_qs)

        page = self.paginate_queryset(task_results)
        if page is not None:
            return self.get_paginated_response(task_results)

        return Response(task_results)
