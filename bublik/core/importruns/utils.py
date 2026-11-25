# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import datetime, timedelta
from functools import wraps
import json
from textwrap import shorten
import time

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
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = timedelta(seconds=time.perf_counter() - self.start_time)
        self.logger.info(f'{self.prefix} is completed in [{elapsed}]')

    def __call__(self, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with MeasureTime(self.prefix):
                return func(*args, **kwargs)

        return wrapper


def runtime(start_time):
    return (datetime.now() - start_time).total_seconds()


def describe_schema_error(error, data):
    """
    Return the debug details of a schema violation in import data: the message,
    the iteration (test, package or session) and the element in it violating
    the schema, and the data of the element or, for a plain value, of its parent.
    """
    iteration, tin, element = [], None, ''
    parent = node = data
    for key in error.absolute_path:
        parent, node = node, node[key]
        if isinstance(node, dict) and node.get('type') in ('test', 'pkg', 'session'):
            iteration.append(node.get('name') or node['type'])
            tin, element = node.get('tin'), ''
        elif isinstance(key, int):
            label = isinstance(node, dict) and (
                node.get('name') or node.get('tool') or node.get('type')
            )
            element += f' #{key + 1}' + (f' ({label})' if label else '')
        else:
            element += f' > {key}'

    details = [f'Error: {error.message}']
    if iteration:
        details.append(f'Iteration: {"/".join(iteration)} (TIN {tin})')
    if element or not iteration:
        details.append(f'Element: {element.removeprefix(" > ") or "the whole data"}')
    shown = node if isinstance(node, (dict, list)) else parent
    details.append(f'Data: {shorten(json.dumps(shown, default=str), 500, placeholder="...")}')
    return details
