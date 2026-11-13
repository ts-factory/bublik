# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from types import SimpleNamespace
from unittest import mock
import uuid

from celery import states
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from bublik.data.models import (
    EventLog,
    ImportJob,
    Job,
    JobTaskExecution,
    JobTaskExecutionResult,
    TaskExecution,
)
from bublik.interfaces.celery import app
from bublik.interfaces.celery.tasks import (
    add_revoked_import_task_event,
    importruns,
    task_started_handler,
)


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

    def test_finished_task_keeps_its_outcome(self):
        for task_status in (
            TaskExecution.StatusChoices.SUCCESS,
            TaskExecution.StatusChoices.FAILURE,
        ):
            with self.subTest(status=task_status):
                job, task_execution = self.create_import_task(task_status)

                self.revoke(job, task_execution, terminated=True)

                self.assertEqual(task_execution.status, task_status)
                self.assertIsNone(task_execution.finished_at)
                self.assertFalse(
                    EventLog.objects.filter(
                        job_task_execution__task_execution=task_execution,
                    ).exists(),
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


@mock.patch.object(app.control, 'revoke')
class RevokeImportTaskApiTest(ImportTaskMixin, APITestCase):
    url = reverse('api-v2:importruns-revoke')

    def test_unfinished_task_is_revoked(self, revoke):
        for task_status in (
            TaskExecution.StatusChoices.RECEIVED,
            TaskExecution.StatusChoices.RUNNING,
        ):
            with self.subTest(status=task_status):
                revoke.reset_mock()
                _, task_execution = self.create_import_task(task_status)
                task_id = str(task_execution.task_id)

                response = self.client.post(self.url, {'task_id': task_id}, format='json')

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    response.data,
                    {'task_id': task_execution.task_id, 'status': 'REVOKED'},
                )
                task_execution.refresh_from_db()
                self.assertEqual(task_execution.status, TaskExecution.StatusChoices.REVOKED)
                revoke.assert_called_once_with(task_id, terminate=True)

    def test_task_is_unchanged_if_broker_fails(self, revoke):
        revoke.side_effect = OSError('broker is unreachable')
        _, task_execution = self.create_import_task(TaskExecution.StatusChoices.RUNNING)

        response = self.client.post(
            self.url,
            {'task_id': str(task_execution.task_id)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        task_execution.refresh_from_db()
        self.assertEqual(task_execution.status, TaskExecution.StatusChoices.RUNNING)

    def test_status_set_meanwhile_is_kept(self, revoke):
        for task_status in (
            TaskExecution.StatusChoices.REVOKED,
            TaskExecution.StatusChoices.SUCCESS,
        ):
            with self.subTest(status=task_status):
                _, task_execution = self.create_import_task(
                    TaskExecution.StatusChoices.RUNNING,
                )

                # The worker or the task itself changes the status meanwhile
                def set_status(*args, pk=task_execution.pk, new_status=task_status, **kwargs):
                    TaskExecution.objects.filter(pk=pk).update(status=new_status)

                revoke.side_effect = set_status

                response = self.client.post(
                    self.url,
                    {'task_id': str(task_execution.task_id)},
                    format='json',
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                task_execution.refresh_from_db()
                self.assertEqual(task_execution.status, task_status)

    def test_finished_task_is_not_revoked(self, revoke):
        for task_status in (
            TaskExecution.StatusChoices.SUCCESS,
            TaskExecution.StatusChoices.FAILURE,
            TaskExecution.StatusChoices.REVOKED,
        ):
            with self.subTest(status=task_status):
                _, task_execution = self.create_import_task(task_status)

                response = self.client.post(
                    self.url,
                    {'task_id': str(task_execution.task_id)},
                    format='json',
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                task_execution.refresh_from_db()
                self.assertEqual(task_execution.status, task_status)
        revoke.assert_not_called()

    def test_unknown_task_is_not_found(self, revoke):
        response = self.client.post(self.url, {'task_id': str(uuid.uuid4())}, format='json')

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        revoke.assert_not_called()

    def test_non_import_task_is_not_found(self, revoke):
        job = Job.objects.create(name=Job.NameChoices.META_CATEGORIZATION)
        task_execution = TaskExecution.objects.create(
            task_id=uuid.uuid4(),
            status=TaskExecution.StatusChoices.RUNNING,
        )
        JobTaskExecution.objects.create(job=job, task_execution=task_execution)

        response = self.client.post(
            self.url,
            {'task_id': str(task_execution.task_id)},
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        task_execution.refresh_from_db()
        self.assertEqual(task_execution.status, TaskExecution.StatusChoices.RUNNING)
        revoke.assert_not_called()

    def test_invalid_task_id_is_rejected(self, revoke):
        response = self.client.post(self.url, {'task_id': 'not-a-uuid'}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        revoke.assert_not_called()


class RevokedQueuedImportTaskTest(ImportTaskMixin, TestCase):
    def test_revoked_task_is_not_marked_as_running(self):
        _, task_execution = self.create_import_task(TaskExecution.StatusChoices.REVOKED)

        task_started_handler(task_id=task_execution.task_id)

        task_execution.refresh_from_db()
        self.assertEqual(task_execution.status, TaskExecution.StatusChoices.REVOKED)
        self.assertIsNone(task_execution.started_at)

    @mock.patch('bublik.core.importruns.import_run.import_run')
    def test_revoked_task_is_not_started(self, import_run):
        job, task_execution = self.create_import_task(TaskExecution.StatusChoices.REVOKED)

        result = importruns.apply(
            args=['http://bublik/', job.id, RUN_SOURCE_URL, None, None, None, False],
            task_id=str(task_execution.task_id),
        )

        self.assertEqual(result.state, states.IGNORED)
        import_run.assert_not_called()
        task_execution.refresh_from_db()
        self.assertEqual(task_execution.status, TaskExecution.StatusChoices.REVOKED)
        self.assertIsNotNone(task_execution.finished_at)
        event = EventLog.objects.get(
            job_task_execution__task_execution=task_execution,
            facility=EventLog.FacilityChoices.CELERY,
        )
        self.assertEqual(
            event.msg,
            f'revoked import {RUN_SOURCE_URL} -- Celery task ID {task_execution.task_id}',
        )
