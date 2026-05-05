# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import datetime
from functools import wraps

from django.core.files import locks

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


class MeasureTime:
    def __init__(self, prefix: str = ''):
        self.prefix = prefix

    def __enter__(self):
        self.logger = get_task_or_server_logger()
        self.logger.info(f'{self.prefix} is started')
        self.start_time = datetime.now()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = datetime.now() - self.start_time
        self.logger.info(f'{self.prefix} is completed in [{elapsed}]')

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with MeasureTime(self.prefix):
                return func(*args, **kwargs)

        return wrapper


def runtime(start_time):
    return (datetime.now() - start_time).total_seconds()
