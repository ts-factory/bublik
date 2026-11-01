# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from types import SimpleNamespace
import uuid

from django.test import TestCase

from bublik.data.models import EventLog, ImportJob, JobTaskExecutionResult, TaskExecution
from bublik.interfaces.celery.tasks import add_revoked_import_task_event


RUN_SOURCE_URL = 'https://logs.example.com/run/'


class ImportTaskMixin:
    def create_import_task(self, status):
        job = ImportJob.objects.create(
            name=ImportJob.NameChoices.IMPORTRUNS,
            url=RUN_SOURCE_URL,
        )
        task_execution = TaskExecution.objects.create(task_id=uuid.uuid4(), status=status)
        JobTaskExecutionResult.objects.create(
            job=job,
            task_execution=task_execution,
            url=RUN_SOURCE_URL,
        )
        return job, task_execution


class RevokedImportTaskEventTest(ImportTaskMixin, TestCase):
    def revoke(self, job, task_execution, terminated):
        add_revoked_import_task_event(
            sender=SimpleNamespace(name='import'),
            request=SimpleNamespace(
                id=str(task_execution.task_id),
                args=['http://bublik/', job.id, RUN_SOURCE_URL],
            ),
            terminated=terminated,
        )
        task_execution.refresh_from_db()

    def test_terminated_task_is_marked_as_revoked(self):
        job, task_execution = self.create_import_task(TaskExecution.StatusChoices.RUNNING)

        self.revoke(job, task_execution, terminated=True)

        self.assertEqual(task_execution.status, TaskExecution.StatusChoices.REVOKED)
        self.assertIsNotNone(task_execution.finished_at)
        event = EventLog.objects.get(job_task_execution__task_execution=task_execution)
        self.assertEqual(event.severity, EventLog.SeverityChoices.WARNING)
        self.assertEqual(
            event.msg,
            f'revoked import {RUN_SOURCE_URL} '
            f'-- Celery task ID {task_execution.task_id} -- terminated',
        )

    def test_queued_task_is_marked_as_revoked(self):
        job, task_execution = self.create_import_task(TaskExecution.StatusChoices.RECEIVED)

        self.revoke(job, task_execution, terminated=False)

        self.assertEqual(task_execution.status, TaskExecution.StatusChoices.REVOKED)
        event = EventLog.objects.get(job_task_execution__task_execution=task_execution)
        self.assertEqual(
            event.msg,
            f'revoked import {RUN_SOURCE_URL} -- Celery task ID {task_execution.task_id}',
        )
