# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import logging
import os
import os.path
import re
import subprocess

from celery.utils.log import get_task_logger
from pythonjsonlogger import jsonlogger

from bublik import settings


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


def parse_log(error_regex, project_regex, logpath, maxlen=5):
    if not os.path.exists(logpath):
        msg = f'Log file {logpath} does not exist'
        raise FileNotFoundError(msg)

    with open(logpath) as f:
        matched = []
        project_name = None

        lines = f.readlines()
        for index, line in enumerate(lines):
            if re.search(error_regex, line):
                preceding_lines = lines[max(0, index - maxlen) : index]
                matched.append('...\n' + ''.join(preceding_lines) + line)
            project_match = re.search(project_regex, line)
            if project_match:
                project_name = project_match.group(1)

        return '\n'.join(matched), project_name


def log_disk_space_usage(logger, msg, options):
    cmd = ['df', options]
    logger.info(msg)
    process = subprocess.run(cmd, check=False, capture_output=True, text=True)
    logger.info(f'{cmd} output:\n{process.stdout}')
