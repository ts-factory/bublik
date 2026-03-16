# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.db import models

from bublik.data.models.result import TestIterationResult


__all__ = [
    'ImportJob',
    'Job',
    'JobTaskExecution',
    'JobTaskExecutionResult',
    'TaskExecution',
]


class Job(models.Model):

    class NameChoices(models.TextChoices):
        IMPORTRUNS = 'importruns'
        META_CATEGORIZATION = 'meta_categorization'

    name = models.CharField(
        choices=NameChoices.choices,
        max_length=64,
        null=False,
        help_text='The name of the job.',
    )

    started_at = models.DateTimeField(
        null=True,
        help_text='Timestamp when the job started.',
    )

    finished_at = models.DateTimeField(
        null=True,
        help_text='Timestamp when the job finished.',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_job'

    def __repr__(self):
        return (
            f'Job(name={self.name!r}, started_at={self.started_at!r}, '
            f'finished_at={self.finished_at!r})'
        )


class ImportJob(Job):

    url = models.TextField(null=False)

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_importjob'

    def __repr__(self):
        return (
            f'ImportJob(name={self.name!r}, started_at={self.started_at!r}, '
            f'finished_at={self.finished_at!r}, url={self.url!r})'
        )


class TaskExecution(models.Model):
    class StatusChoices(models.TextChoices):
        RECEIVED = 'RECEIVED'
        RUNNING = 'RUNNING'
        SUCCESS = 'SUCCESS'
        FAILURE = 'FAILURE'

    task_id = models.UUIDField(
        unique=True,
        db_index=True,
        help_text='Celery task UUID.',
    )
    status = models.CharField(
        choices=StatusChoices.choices,
        db_index=True,
        max_length=64,
        null=True,
        help_text='Current execution state of the task.',
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the task started execution.',
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Timestamp when the task finished.',
    )

    class Meta:
        db_table = 'bublik_taskexecution'

    def __repr__(self):
        return (
            f'TaskExecution(pk={self.pk!r}, task_id={self.task_id!r}, '
            f'status={self.status!r}, started_at={self.started_at!r}, '
            f'finished_at={self.finished_at!r})'
        )


class JobTaskExecution(models.Model):
    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        help_text='The job identifier.',
    )
    task_execution = models.OneToOneField(TaskExecution, null=True, on_delete=models.CASCADE)

    class Meta:
        db_table = 'bublik_jobtaskexecution'
        unique_together = ('job', 'task_execution')
        constraints = [
            models.UniqueConstraint(
                fields=['job'],
                condition=models.Q(task_execution__isnull=True),
                name='unique_null_task_execution_per_job',
            ),
        ]

    def __repr__(self):
        return (
            f'JobTaskExecution(pk={self.pk!r}, job_id={self.job_id!r}, '
            f'task_execution_id={self.task_execution_id!r})'
        )


class JobTaskExecutionResult(JobTaskExecution):
    run = models.ForeignKey(
        TestIterationResult,
        null=True,
        on_delete=models.SET_NULL,
        help_text='The run identifier.',
    )
    url = models.TextField(help_text='The run source URL.')

    class Meta:
        db_table = 'bublik_jobtaskexecutionresult'

    def __repr__(self):
        return (
            f'JobTaskExecutionResult(pk={self.pk!r}, job_id={self.job_id!r}, '
            f'task_execution_id={self.task_execution_id!r}, run_id={self.run_id!r}, '
            f'url={self.url!r})'
        )
