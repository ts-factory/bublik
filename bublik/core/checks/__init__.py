# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from bublik.core.url import get_url


# Basic functions:


def modify_msg(msg, run_url=None):
    if run_url:
        msg += f', ignoring {run_url}'
    return msg


def check_attr(what, where, msg=None, logger=None):
    if not hasattr(where, what):
        if logger:
            logger.error(msg)
        return False
    return True


def check_file(what, msg=None, logger=None):
    if not os.path.isfile(what):
        if logger:
            logger.error(msg)
        return False
    return True


def check_dir(what, msg=None, logger=None):
    if not os.path.isdir(what):
        if logger:
            logger.error(msg)
        return False
    return True


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


def check_settings(what, logger=None, url=None):
    from bublik import settings

    msg = modify_msg(f'{what} wasn\'t find in bublik/settings.py', url)
    return check_attr(what, settings, msg, logger)


def check_conf_file(file, logger=None, url=None):
    from bublik import settings

    msg = modify_msg(f'{file} wasn\'t find among project conf files', url)
    what = os.path.join(settings.PER_CONF_DIR, file)
    return check_file(what, msg, logger)


def check_run_file(file, run, logger=None, required=False):
    return check_url(
        what=os.path.join(run, file),
        msg=f"{file} wasn't found in {run}",
        logger=logger,
        required=required,
    )


def check_files_for_categorization(logger=None, url=None, conf_files=None):

    if conf_files is None:
        conf_files = ['tags.conf', 'meta.conf']
    if not check_settings('PER_CONF_DIR', url, logger):
        msg = 'unable to get PER_CONF_DIR'
        raise AttributeError(msg)

    for file in conf_files:
        if not check_conf_file(file, logger, url):
            msg = f'unable to get {file}'
            raise FileNotFoundError(msg)
