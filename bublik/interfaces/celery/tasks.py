# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import os
import subprocess
from urllib.parse import urljoin

from celery.signals import (
    after_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_received,
    task_success,
)
from django.core.management import call_command

from bublik import settings
from bublik.core.importruns.utils import normalize_importruns_params
from bublik.core.logging import get_task_or_server_logger, parse_log
from bublik.core.mail import send_importruns_failed_mail
from bublik.core.utils import create_event, get_import_job_task
from bublik.data.models import EventLog, Project, TaskExecution, TestIterationResult
from bublik.interfaces.celery import app


@after_task_publish.connect()
def add_received_import_task_event(sender=None, headers=None, body=None, **kwargs):
    task_id = headers['id']
    TaskExecution.objects.update_or_create(
        task_id=task_id,
        defaults={
            'status': TaskExecution.StatusChoices.RECEIVED,
        },
    )

    args = body[0]
    job_id = args[1] if args else None
    url = args[2] if args else None
    job_task_execution = get_import_job_task(
        job_id=job_id,
        task_id=task_id,
        run_url=url,
    )

    msg = ' '.join(
        filter(None, (f'received {sender}', url, f'-- Celery task ID {task_id}')),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.INFO,
        msg=msg,
        job_task_execution=job_task_execution,
    )


@task_received.connect()
def add_started_import_task_event(sender=None, request=None, headers=None, body=None, **kwargs):
    task_id = request.id

    args = request.args
    job_id = args[1] if args else None
    url = args[2] if args else None
    job_task_execution = get_import_job_task(
        job_id=job_id,
        task_id=task_id,
        run_url=url,
    )

    msg = ' '.join(
        filter(
            None,
            (f'started processing {request.name}', url, f'-- Celery task ID {task_id}'),
        ),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.INFO,
        msg=msg,
        job_task_execution=job_task_execution,
    )


@task_prerun.connect()
def task_started_handler(sender=None, task_id=None, **kwargs):
    TaskExecution.objects.filter(task_id=task_id).update(
        status=TaskExecution.StatusChoices.RUNNING,
        started_at=datetime.now(),
    )


@task_success.connect()
def add_successful_import_task_event(sender=None, headers=None, body=None, **kwargs):
    task_id = sender.request.id
    TaskExecution.objects.filter(task_id=task_id).update(
        status=TaskExecution.StatusChoices.SUCCESS,
    )

    args = sender.request.args
    job_id = args[1] if args else None
    url = args[2] if args else None
    job_task_execution = get_import_job_task(
        job_id=job_id,
        task_id=task_id,
        run_url=url,
    )

    msg = ' '.join(
        filter(
            None,
            (f'successful {sender.name}', url, f'-- Celery task ID {task_id}'),
        ),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.INFO,
        msg=msg,
        job_task_execution=job_task_execution,
    )


@task_failure.connect()
def add_failed_import_task_event(sender=None, headers=None, body=None, **kwargs):
    task_id = sender.request.id
    TaskExecution.objects.filter(task_id=task_id).update(
        status=TaskExecution.StatusChoices.FAILURE,
    )

    args = sender.request.args
    job_id = args[1] if args else None
    url = args[2] if args else None
    job_task_execution = get_import_job_task(
        job_id=job_id,
        task_id=task_id,
        run_url=url,
    )

    exception = kwargs['exception']
    error_data = getattr(exception, 'message', type(exception).__name__)
    msg = ' '.join(
        filter(
            None,
            (
                f'failed {sender.name}',
                url,
                f'-- Celery task ID {task_id} -- Error: {error_data}',
            ),
        ),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.ERR,
        msg=msg,
        job_task_execution=job_task_execution,
    )


@task_postrun.connect()
def task_finished_handler(sender=None, task_id=None, **kwargs):
    TaskExecution.objects.filter(task_id=task_id).update(finished_at=datetime.now())


@app.task(bind=True, name='import', acks_late=True, reject_on_worker_lost=True)
def importruns(
    self,
    requesting_host,
    job_id,
    run_url,
    project_name,
    date_from,
    date_to,
    force,
):
    task_id = self.request.id
    os.environ['TASK_ID'] = task_id

    logger = get_task_or_server_logger()
    logpath = logger.handlers[0].logpath

    # To avoid cyclic dependency between importruns.py and this module
    from bublik.core.importruns.import_run import import_run

    try:
        query_url = urljoin(
            requesting_host,
            f'importruns/source/?from={date_from}&to={date_to}&url={run_url}'
            f'&force={force}&project_name={project_name}',
        )

        logger.info('importruns task started:')
        logger.info(f'[ID]:   {task_id}')
        logger.info(f'[FROM]: {date_from or ""}')
        logger.info(f'[TO]:   {date_to or ""}')
        logger.info(f'[URL]:  {run_url}')
        logger.info(f'[RUN]:  curl {query_url}')

        importruns_params = normalize_importruns_params(
            run_url=run_url,
            project_name=project_name,
            date_from=date_from,
            date_to=date_to,
            force=force,
        )

        import_run(
            job_id=job_id,
            task_id=task_id,
            **importruns_params,
        )

        return task_id

    except Exception as e:
        error_data = getattr(e, 'message', type(e).__name__)
        logger.error(
            f'Importruns failed: {error_data}',
        )
        raise

    finally:
        # To send email in case of any error, not only when some occur
        # initiating importruns task

        add_to_message = None

        try:
            errors, log_project_name = parse_log(
                r'"ERROR"',
                r'the project name is ([^"]+)',
                logpath,
            )
            project_name = log_project_name or project_name
            project_id = (
                Project.objects.filter(name=project_name).values_list('id', flat=True).first()
                if project_name
                else None
            )

            if errors:
                add_to_message = f'Preview:\n{errors}'
                send_importruns_failed_mail(
                    requesting_host,
                    project_id,
                    task_id,
                    run_url,
                    add_to_message,
                )

        except Exception as e:
            logger.warning(e)


@app.task(bind=True)
def meta_categorization(self, project_name):
    task_id = self.request.id
    os.environ['TASK_ID'] = task_id

    logger = get_task_or_server_logger()
    logpath = logger.handlers[0].logpath

    query_url = f"curl 'http://{settings.BUBLIK_HOST}/meta_categorization/'"

    logger.info('meta categorization task started:')
    logger.info(f'[RUN]:  {query_url}')

    with open(logpath, 'a') as f:
        call_command('meta_categorization', project_name=project_name, stdout=f, stderr=f)

    return task_id


@app.task(bind=True)
def run_cache(self, *args, **kwargs):
    '''This is a wrapper to execute an action in the background.'''
    call_command('run_cache', *args, **kwargs)


@app.task(bind=True)
def update_all_hashed_objects(self):
    '''This is a wrapper to execute an action in the background.'''
    call_command('update_all_hashed_objects')


@app.task(bind=True)
def clear_all_runs_stats_cache(self):
    task_id = self.request.id
    os.environ['TASK_ID'] = task_id

    logger = get_task_or_server_logger()
    logpath = logger.handlers[0].logpath

    query_url = f"curl 'http://{settings.BUBLIK_HOST}/clear_all_runs_stats_cache/'"

    logger.info('clear all runs stats cache task started:')
    logger.info(f'[RUN]:  {query_url}')

    test_run_ids = list(
        TestIterationResult.objects.order_by('test_run_id')
        .distinct('test_run_id')
        .values_list('test_run_id', flat=True),
    )
    test_run_ids = [f'-i {trid}' for trid in test_run_ids if trid is not None]

    with open(logpath, 'a') as log:
        cmd_run_cache = [
            './manage.py',
            'run_cache',
            'delete',
            '-d',
            'stats',
            '--logger_out',
            'True',
        ]
        subprocess.run(
            [*cmd_run_cache, *test_run_ids],
            stdout=log,
            stderr=log,
            shell=False,
            check=True,
        )
        log.flush()

    return task_id
