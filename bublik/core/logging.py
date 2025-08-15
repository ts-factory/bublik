# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path
import re
import subprocess


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
    process = subprocess.run(cmd, capture_output=True, text=True)
    logger.info(f'{cmd} output:\n{process.stdout}')
