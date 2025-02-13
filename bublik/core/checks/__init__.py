# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from bublik.core.url import get_url


# Basic functions:


def modify_msg(msg, run_url=None):
    if run_url:
        msg += f', ignoring {run_url}'
    return msg


def check_url(what, required=False, msg=None, logger=None):
    req = get_url(what, raise_for_status=False)
    ok_code = 200
    if req.status_code == ok_code:
        return True
    if logger:
        if required:
            logger.error(msg)
        else:
            logger.info(msg)
    return False


# Special functions:


def check_run_file(file, run, logger=None, required=False):
    return check_url(
        what=os.path.join(run, file),
        msg=f"{file} wasn't found in {run}",
        logger=logger,
        required=required,
    )
