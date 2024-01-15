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
from bublik.core.checks import check_files_for_categorization
from bublik.core.logging import parse_log
from bublik.core.mail import send_importruns_failed_mail
from bublik.core.utils import create_event
from bublik.data.models import EventLog
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
):

    task_id = self.request.id
    logger, logpath = get_or_create_task_logger(task_id)
    check_files_for_categorization(logger, param_url)

    cmd_import = ['./manage.py', 'importruns']

    try:
        query_url = urljoin(
            requesting_host,
            f'importruns/source/?from={param_from}&to={param_to}&url={param_url}&force={param_force}',
        )

        if param_from:
            cmd_import += ['--from', param_from]
        if param_to:
            cmd_import += ['--to', param_to]
        if param_url:
            cmd_import += ['--id', task_id]
        if param_force:
            cmd_import += ['--force', param_force]

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
            errors = parse_log(r'"ERROR"', logpath)
            if errors:
                add_to_message = f'Preview:\n{errors}'
                send_importruns_failed_mail(requesting_host, task_id, param_url, add_to_message)

        except Exception as e:
            logger.warning(e)


@app.task(bind=True)
def meta_categorization(self):
    task_id = self.request.id
    logger, logpath = get_or_create_task_logger(task_id)

    query_url = f"curl 'http://{settings.BUBLIK_HOST}/meta_categorization/'"

    logger.info('meta categorization task started:')
    logger.info(f'[RUN]:  {query_url}')

    check_files_for_categorization(logger)

    with open(logpath, 'a') as f:
        call_command('meta_categorization', stdout=f, stderr=f)

    return task_id


@app.task(bind=True)
def run_cache(self, *args, **kwargs):
    '''This is a wrapper to execute an action in the background.'''
    call_command('run_cache', *args, **kwargs)


@app.task(bind=True)
def update_all_hashed_objects(self):
    '''This is a wrapper to execute an action in the background.'''
    call_command('update_all_hashed_objects')
