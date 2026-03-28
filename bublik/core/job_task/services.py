# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import TYPE_CHECKING

from django.contrib.postgres.aggregates import ArrayAgg
from django.db.models import DurationField, ExpressionWrapper, F
from django.db.models.functions import Coalesce, JSONObject, Now

from bublik.core.exceptions import NotFoundError
from bublik.data.models import (
    Job,
    JobTaskExecutionResult,
    TaskExecution,
)


if TYPE_CHECKING:
    from datetime import datetime, timedelta


@dataclass
class TaskExecutionResult:
    status: str
    run_source_url: str
    celery_task: str
    started_at: datetime | None
    finished_at: datetime | None
    runtime: timedelta | None
    job_id: int
    run_id: int | None
    event_logs: list
    error_msg: str | None


@dataclass
class TaskSummary:
    status: str
    run_source_url: str
    celery_task: str
    run_id: int | None


class JobTaskExecutionService:
    @staticmethod
    def list_job_task_queryset():
        return JobTaskExecutionResult.objects.filter(
            job__name=Job.NameChoices.IMPORTRUNS,
            task_execution__isnull=False,
        ).annotate(
            celery_task=F('task_execution__task_id'),
            status=F('task_execution__status'),
            run_source_url=F('url'),
        )

    @staticmethod
    def _extract_error(event_logs: list[dict]) -> str | None:
        '''
        Extract error message from event logs if present.

        Args:
            event_logs: List of event log dicts with 'msg' key

        Returns:
            Extracted error string, or None
        '''
        pattern = r'--\s*Error:\s*(.*?)(?:\s*--|$)'
        for event_log in event_logs:
            msg = event_log.get('msg', '')
            if m := re.search(pattern, msg):
                return m.group(1)
        return None

    @staticmethod
    def get_tasks_by_job(job_id: int) -> list[TaskSummary]:
        '''
        Get all task execution results for a given job ID.

        Args:
            job_id: The ID of the job

        Returns:
            List of TaskSummary with status, run_source_url, celery_task, run_id

        Raises:
            NotFoundError: if no tasks found for the given job
        '''
        task_results = list(
            JobTaskExecutionService.list_job_task_queryset()
            .filter(job_id=job_id)
            .order_by('id')
            .values(
                'status',
                'run_source_url',
                'celery_task',
                'run_id',
            ),
        )

        if not task_results:
            msg = "Tasks corresponding to the passed job doesn't exist"
            raise NotFoundError(msg)

        return [TaskSummary(**tr) for tr in task_results]

    @staticmethod
    def list_tasks(filtered_queryset) -> list[TaskExecutionResult]:
        '''
        Annotate and return a list of task execution results with
        timing, event logs, and extracted error messages.

        Args:
            filtered_queryset: Pre-filtered queryset of JobTaskExecutionResult

        Returns:
            List of TaskExecutionResult with full task details including error_msg

        Raises:
            NotFoundError: if no tasks match the given parameters
        '''
        task_results = list(
            filtered_queryset.order_by('-id')
            .annotate(
                started_at=F('task_execution__started_at'),
                finished_at=F('task_execution__finished_at'),
                runtime=ExpressionWrapper(
                    Coalesce(F('task_execution__finished_at'), Now())
                    - F('task_execution__started_at'),
                    output_field=DurationField(),
                ),
                event_logs=ArrayAgg(
                    JSONObject(
                        timestamp=F('eventlog__timestamp'),
                        facility=F('eventlog__facility'),
                        severity=F('eventlog__severity'),
                        msg=F('eventlog__msg'),
                    ),
                    ordering='eventlog__timestamp',
                ),
            )
            .values(
                'status',
                'run_source_url',
                'celery_task',
                'started_at',
                'finished_at',
                'runtime',
                'job_id',
                'run_id',
                'event_logs',
            ),
        )

        if not task_results:
            msg = "Tasks corresponding to the passed parameters doesn't exist"
            raise NotFoundError(msg)

        return [
            TaskExecutionResult(
                **tr,
                error_msg=(
                    JobTaskExecutionService._extract_error(tr['event_logs'])
                    if tr['status'] == TaskExecution.StatusChoices.FAILURE
                    else None
                ),
            )
            for tr in task_results
        ]
