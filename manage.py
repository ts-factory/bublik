#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os
import sys


if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError:
        msg = 'Error occurred while importing django.core.management.execute_from_command_line'
        raise ImportError(
            msg,
        ) from ImportError
    execute_from_command_line(sys.argv)
