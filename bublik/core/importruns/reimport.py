# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

"""
Importing runs again with force, gradually, so that other imports are not held up.
Each run is imported into the project it belongs to.

The runs are selected by criteria (see RunCriterion and RUN_CRITERIA), and their
imports are scheduled one by one: no more than a given number of them run at
a time, and the next one is scheduled only when no other task is waiting in the
Celery queue. A fresh import thus waits at most for the imports already running.
An import running too long is revoked, so that it frees its worker process for
the next one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import time
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urljoin
from uuid import UUID

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from bublik.core.exceptions import BublikAPIError
from bublik.core.importruns.source.run_traversal import schedule_run_import
from bublik.core.job_task.services import JobTaskExecutionService
from bublik.core.run.data import find_duplicates
from bublik.core.run.external_links import get_sources
from bublik.core.utils import create_import_job
from bublik.data.models import (
    Expectation,
    JobTaskExecutionResult,
    TaskExecution,
    TestIterationResult,
)
from bublik.interfaces.celery import app


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from django.db.models import QuerySet


class RunCriterion:
    """
    A criterion for selecting runs to import again.

    To add one, subclass it, set the name it is chosen by and its description,
    implement filter(), and add an instance to RUN_CRITERIA.
    """

    name: ClassVar[str]
    help: ClassVar[str]

    def filter(self, runs: QuerySet) -> QuerySet:
        """Return the runs of the given ones that meet the criterion."""
        raise NotImplementedError


class MissingTagExpression(RunCriterion):
    name = 'missing-tag-expression'
    help = (
        'Runs with an unexpected result whose expected results have no tag expression: '
        'from v1.10.0, the import of run logs did not save it for unexpected results. '
        'Expected results of TRC "default" entries have no tag expression either, '
        'so runs having them are selected too.'
    )

    def filter(self, runs):
        untagged_links = Expectation.results.through.objects.filter(
            testiterationresult_id=OuterRef('pk'),
        ).exclude(expectation__expectmeta__meta__type='tag_expression')
        untagged_unexpected = TestIterationResult.objects.filter(
            test_run_id=OuterRef('pk'),
            meta_results__meta__type='err',
        ).filter(Exists(untagged_links))
        return runs.filter(Exists(untagged_unexpected))


class UnremovableDuplicates(RunCriterion):
    name = 'unremovable-duplicates'
    help = (
        'Runs with duplicated expected results of different imports that each carry '
        'data of their own, which fix_expected_results cannot remove.'
    )

    def filter(self, runs):
        run_ids = list(runs.values_list('id', flat=True))
        unremovable = {run_id for run_id, _, _, _, mixed in find_duplicates(run_ids) if mixed}
        return runs.filter(id__in=unremovable)


RUN_CRITERIA = {
    criterion.name: criterion for criterion in (MissingTagExpression(), UnremovableDuplicates())
}


def imported_after(runs: QuerySet, moment: datetime) -> QuerySet:
    """Return the runs of the given ones imported successfully since the moment."""
    imports = JobTaskExecutionResult.objects.filter(
        run_id=OuterRef('pk'),
        task_execution__status=TaskExecution.StatusChoices.SUCCESS,
        task_execution__started_at__gte=moment,
    )
    return runs.filter(Exists(imports))


@dataclass
class ReimportReport:
    succeeded: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    timed_out: list[int] = field(default_factory=list)
    revoked: list[int] = field(default_factory=list)
    no_source: list[int] = field(default_factory=list)
    running: list[int] = field(default_factory=list)
    left: list[int] = field(default_factory=list)


class GradualReimport:
    """
    Import the given runs again with force, scheduling the imports gradually.

    @notify: called as notify(message) or notify(message, outcome) on each
        change, where outcome is 'success', 'error' or 'warning'.
    @max_running: the most imports of the runs running at a time. It is lowered
        to leave other imports at least one Celery worker process if the number
        of the processes is known.
    @hours: (start, end) hours of the day in UTC (TIME_ZONE) to schedule imports
        in, the end hour excluded, e.g. (22, 7) for 22:00-07:00; None to schedule
        them at any time.
    @poll_interval: seconds between checks of the imports and the queue.
    @task_timeout: how long an import may run before it is revoked.
    """

    def __init__(
        self,
        run_ids: Iterable[int],
        notify: Callable[..., None],
        max_running: int = 2,
        hours: tuple[int, int] | None = None,
        poll_interval: float = 30,
        task_timeout: timedelta = timedelta(hours=3),
    ):
        self.pending = list(run_ids)
        self.total = len(self.pending)
        # Run ID -> its position among the runs, to show in the messages
        self.positions = {run_id: i for i, run_id in enumerate(self.pending, 1)}
        self.max_running = max_running
        self.hours = hours
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout
        self.notify = notify
        self.report = ReimportReport()
        # Celery task ID -> run ID
        self.running: dict[str, int] = {}
        self.queue_check_failed = False
        # Bublik URL for the links in import failure emails, as a request
        # would give it (see get_current_scheme_host_prefix())
        self.requesting_host = (
            urljoin(f'http://{settings.BUBLIK_HOST}', settings.URL_PREFIX).rstrip('/') + '/'
        )

    def run(self) -> ReimportReport:
        """
        Import the runs and return the report. If interrupted, the report
        is still complete: the runs not scheduled yet are listed as left,
        and those being imported as running (their imports go on).
        """
        self.limit_to_workers()
        try:
            while self.pending or self.running:
                self.collect_finished()
                if self.pending and self.can_schedule():
                    # The run is removed from the pending ones only once it is
                    # scheduled, so that it is reported if scheduling fails
                    self.schedule(self.pending[0])
                    self.pending.pop(0)
                    # The queue is checked again before the next one
                    continue
                time.sleep(self.poll_interval)
        finally:
            self.report.running = list(self.running.values())
            self.report.left = [
                run_id for run_id in self.pending if run_id not in self.report.running
            ]
        return self.report

    def limit_to_workers(self):
        """
        Lower the limit of running imports to leave other imports at least one
        Celery worker process, if the number of the processes is known.
        """
        try:
            stats = app.control.inspect(timeout=5).stats() or {}
            processes = sum(worker['pool']['max-concurrency'] for worker in stats.values())
        except Exception as e:
            self.notify(f'cannot get the number of Celery worker processes ({e})')
            return
        if not processes:
            self.notify('no Celery workers answered: imports will not run until one starts')
            return

        limit = max(processes - 1, 1)
        if self.max_running > limit:
            self.notify(
                f'Celery has {processes} worker process(es): no more than {limit} '
                f'import(s) of the runs will run at a time instead of {self.max_running}',
            )
            self.max_running = limit
        if processes == 1:
            self.notify(
                'with a single Celery worker process, other imports wait for '
                'an import of the runs running',
            )

    def can_schedule(self) -> bool:
        return (
            len(self.running) < self.max_running
            and self.in_hours(timezone.localtime())
            and self.queue_is_empty()
        )

    def in_hours(self, now: datetime) -> bool:
        if self.hours is None:
            return True
        start, end = self.hours
        if start <= end:
            return start <= now.hour < end
        return now.hour >= start or now.hour < end

    def queue_is_empty(self) -> bool:
        """
        Whether no task waits in the Celery queue. If the queue cannot be
        checked, imports are scheduled with only the limit of running ones.
        """
        try:
            with app.connection_for_read() as connection:
                queue = connection.default_channel.queue_declare(
                    queue=app.conf.task_default_queue,
                    passive=True,
                )
                return queue.message_count == 0
        except Exception as e:
            if not self.queue_check_failed:
                self.queue_check_failed = True
                self.notify(
                    f'cannot check the Celery queue ({e}): imports are scheduled '
                    'with only the limit of running ones',
                )
            return True

    def schedule(self, run_id: int):
        run_source_url = get_sources(run_id)
        if not run_source_url:
            self.report.no_source.append(run_id)
            self.notify(
                f'{self.position(run_id)} run {run_id}: no logs source found, skipped',
                'warning',
            )
            return

        # The run is imported into the project it belongs to: the project
        # in meta_data.json may differ (e.g. be renamed since) or be missing
        project_name = (
            TestIterationResult.objects.filter(id=run_id)
            .values_list('project__name', flat=True)
            .first()
        )

        import_job = create_import_job(run_source_url)
        try:
            task_id = schedule_run_import(
                self.requesting_host,
                import_job.id,
                run_source_url,
                project_name=project_name,
                date_from=None,
                date_to=None,
                force=True,
            )
            self.running[str(task_id)] = run_id
        finally:
            import_job.finished_at = timezone.now()
            import_job.save()

        self.notify(f'{self.position(run_id)} run {run_id}: import scheduled as task {task_id}')

    def collect_finished(self):
        if not self.running:
            return

        executions = {
            str(task_id): (status, started_at)
            for task_id, status, started_at in TaskExecution.objects.filter(
                task_id__in=list(self.running),
            ).values_list('task_id', 'status', 'started_at')
        }
        now = timezone.now()
        for task_id, run_id in list(self.running.items()):
            status, started_at = executions.get(task_id, (None, None))
            if status == TaskExecution.StatusChoices.SUCCESS:
                del self.running[task_id]
                self.report.succeeded.append(run_id)
                self.notify(f'{self.position(run_id)} run {run_id}: imported', 'success')
            elif status == TaskExecution.StatusChoices.FAILURE:
                del self.running[task_id]
                self.report.failed.append(run_id)
                self.notify(
                    f'{self.position(run_id)} run {run_id}: import failed (task {task_id})',
                    'error',
                )
            elif status == TaskExecution.StatusChoices.REVOKED:
                del self.running[task_id]
                self.report.revoked.append(run_id)
                self.notify(
                    f'{self.position(run_id)} run {run_id}: import revoked (task {task_id})',
                    'error',
                )
            elif (
                status == TaskExecution.StatusChoices.RUNNING
                and started_at is not None
                and now - started_at > self.task_timeout
            ):
                self.revoke(task_id, run_id)

    def revoke(self, task_id: str, run_id: int):
        try:
            JobTaskExecutionService.revoke_task(UUID(task_id))
        except BublikAPIError:
            # The import has just finished: it is collected on the next check
            return
        del self.running[task_id]
        self.report.timed_out.append(run_id)
        self.notify(
            f'{self.position(run_id)} run {run_id}: import not finished in '
            f'{self.task_timeout} (task {task_id}), revoked',
            'error',
        )

    def position(self, run_id: int) -> str:
        """The run's position among the runs, e.g. [3/23] for the third of 23."""
        return f'[{self.positions[run_id]}/{self.total}]'
