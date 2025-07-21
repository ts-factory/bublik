# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import logging
import os
import subprocess
from urllib.parse import urljoin

from celery.signals import after_task_publish, task_failure, task_received, task_success
from celery.utils.log import get_task_logger
from django.core.management import call_command
from pythonjsonlogger import jsonlogger

from bublik import settings
from bublik.core.logging import parse_log
from bublik.core.mail import send_importruns_failed_mail
from bublik.core.utils import create_event
from bublik.data.models import EventLog, Project, TestIterationResult
from bublik.interfaces.celery import app


def get_or_create_task_logger(task_id):
    '''A helper function to create function specific logger lazily.'''

    date_folder = os.path.join(
        settings.MANAGEMENT_COMMANDS_LOG,
        datetime.now().date().strftime('runs_%Y.%m.%d'),
    )
    if not os.path.exists(date_folder):
        os.makedirs(date_folder)

    logpath = os.path.join(date_folder, task_id)

    # Every logger in the celery package inherits from the "celery" logger,
    # and every task logger inherits from the "celery.task" logger.
    logger = get_task_logger(task_id)
    handler = logging.FileHandler(logpath)

    formatter = jsonlogger.JsonFormatter(settings.LOGGING['formatters']['json']['format'])
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger, logpath


@after_task_publish.connect()
def add_received_import_task_event(sender=None, headers=None, body=None, **kwargs):
    body_0_0 = body[0][0] if body[0] else None
    msg = ' '.join(
        filter(None, (f'received {sender}', body_0_0, f'-- Celery task ID {headers["id"]}')),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.INFO,
        msg=msg,
    )


@task_received.connect()
def add_started_import_task_event(sender=None, headers=None, body=None, **kwargs):
    request = kwargs['request']
    args = request.args[0] if request.args else None
    msg = ' '.join(
        filter(
            None,
            (f'started processing {request.name}', args, f'-- Celery task ID {request.id}'),
        ),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.INFO,
        msg=msg,
    )


@task_success.connect()
def add_successful_import_task_event(sender=None, headers=None, body=None, **kwargs):
    args = sender.request.args[0] if sender.request.args else None
    msg = ' '.join(
        filter(
            None,
            (f'successful {sender.name}', args, f'-- Celery task ID {kwargs["result"]}'),
        ),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.INFO,
        msg=msg,
    )


@task_failure.connect()
def add_failed_import_task_event(sender=None, headers=None, body=None, **kwargs):
    args = sender.request.args[0] if sender.request.args else None
    msg = ' '.join(
        filter(
            None,
            (
                f'failed {sender.name}',
                args,
                f'-- Celery task ID {kwargs["task_id"]} -- Error: {kwargs["exception"]}',
            ),
        ),
    )
    create_event(
        facility=EventLog.FacilityChoices.CELERY,
        severity=EventLog.SeverityChoices.ERR,
        msg=msg,
    )


@app.task(bind=True, name='import')
def importruns(
    self,
    param_url,
    param_force,
    param_from=None,
    param_to=None,
    requesting_host=None,
    param_project=None,
):

    task_id = self.request.id
    logger, logpath = get_or_create_task_logger(task_id)

    cmd_import = ['./manage.py', 'importruns']

    try:
        query_url = urljoin(
            requesting_host,
            f'importruns/source/?from={param_from}&to={param_to}&url={param_url}&force={param_force}&prj={param_project}',
        )

        if param_from:
            cmd_import += ['--from', param_from]
        if param_to:
            cmd_import += ['--to', param_to]
        if param_project:
            cmd_import += ['--prj', param_project]
        if param_force:
            cmd_import += ['--force', param_force]
        if param_url:
            cmd_import += ['--id', task_id]

            logger.info('importruns task started:')
            logger.info(f'[ID]:   {task_id}')
            logger.info(f'[FROM]: {param_from}')
            logger.info(f'[TO]:   {param_to}')
            logger.info(f'[URL]:  {param_url}')
            logger.info(f'[RUN]:  curl {query_url}')

            with open(logpath, 'a') as log:
                subprocess.run(
                    [*cmd_import, param_url],
                    stdout=log,
                    stderr=log,
                    shell=False,
                    check=True,
                )
                log.flush()
            return task_id
        msg = 'Invalid parameters for the `importruns`'
        raise AttributeError(msg)

    except Exception as e:
        logger.error(e)
        raise

    finally:
        # To send email in case of any error, not only when some occur
        # initiating importruns task

        add_to_message = None

        try:
            errors, project_name = parse_log(
                r'"ERROR"',
                r'the project name is ([^"]+)',
                logpath,
            )
            project_name = project_name or param_project
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
                    param_url,
                    add_to_message,
                )

        except Exception as e:
            logger.warning(e)


@app.task(bind=True)
def meta_categorization(self, project_name):
    task_id = self.request.id
    logger, logpath = get_or_create_task_logger(task_id)

    query_url = f"curl 'http://{settings.BUBLIK_HOST}/meta_categorization/'"

    logger.info('meta categorization task started:')
    logger.info(f'[RUN]:  {query_url}')

    with open(logpath, 'a') as f:
        call_command('meta_categorization', project=project_name, stdout=f, stderr=f)

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
    logger, logpath = get_or_create_task_logger(task_id)

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
