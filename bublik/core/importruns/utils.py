# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from argparse import ArgumentTypeError
from datetime import datetime

from django.core.files import locks

from bublik.core.argparse import (
    parser_type_date,
    parser_type_force,
    parser_type_url,
)
from bublik.core.exceptions import ImportrunsError
from bublik.core.logging import get_task_or_server_logger


# This is a test for UUID4 collisions
def indicate_collision(task_id, url):
    status = False
    log_file = 'logs/uuid_collision_test'
    uuid_info = f'UUID: {task_id}'

    with open(log_file, 'a+') as f:
        # Safe for multithreading
        locks.lock(f, locks.LOCK_EX)

        # Check collision
        f.seek(0)
        status = any(uuid_info in line for line in f.readlines())

        # Log details
        f.write(
            '    '.join(
                [
                    f'Timestamp: {datetime.now()}',
                    f'Collision: {status}',
                    f'{uuid_info}',
                    f'URL: {url}',
                ],
            )
            + '\n',
        )

    # To call the task againg if collision
    return status


def measure_time(prefix: str = ''):
    def decorator(func):
        def wrapper(*args, **kwargs):
            logger = get_task_or_server_logger()
            logger.info(f'{prefix} is started')
            start_time = datetime.now()
            try:
                return func(*args, **kwargs)
            finally:
                elapsed = datetime.now() - start_time
                logger.info(f'{prefix} is completed in [{elapsed}]')

        return wrapper

    return decorator


def runtime(start_time):
    return (datetime.now() - start_time).total_seconds()


def normalize_importruns_params(
    url,
    project_name=None,
    date_from=None,
    date_to=None,
    force=None,
):
    try:
        return {
            'url': parser_type_url(url),
            'project_name': project_name,
            'date_from': parser_type_date(date_from) if date_from is not None else datetime.min,
            'date_to': parser_type_date(date_to) if date_to is not None else datetime.max,
            'force': parser_type_force(force) if force is not None else False,
        }

    except ArgumentTypeError as exc:
        raise ImportrunsError(
            message='invalid parameters for importruns command',
        ) from exc
