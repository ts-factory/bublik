# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime

from django.core.files import locks


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
