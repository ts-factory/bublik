# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os

import requests
from requests_kerberos import DISABLED, HTTPKerberosAuth

from bublik.core.logging import get_task_or_server_logger


logger = get_task_or_server_logger()


SAVE_URL_CHUNK_SIZE = 16384


def get_url(url_str, raise_for_status=True, quiet_404=False):
    kerberos_auth = HTTPKerberosAuth(mutual_authentication=DISABLED)
    req = requests.get(url_str, auth=kerberos_auth)

    # CGI uses 302 status code for auto generated files and
    # requests lib doesn't authenticate on retry.
    # Do retry here when auto generated file is in place.
    not_auth = 401
    if req.status_code == not_auth:
        req = requests.get(url_str, auth=kerberos_auth)

    if raise_for_status:
        not_found_code = 404
        if quiet_404 and req.status_code == not_found_code:
            return None
        req.raise_for_status()
    return req


def fetch_url(url_str, quiet_404=False):
    try:
        req = get_url(url_str, quiet_404=quiet_404)
        if req is None:
            return None
    except requests.exceptions.HTTPError as e:
        logger.error(e)
        return None

    return req.text


def save_url_to_fd(url_str, fd_out, quiet_404=False):
    try:
        req = get_url(url_str, quiet_404=quiet_404)
        if req is None:
            return False
    except requests.exceptions.HTTPError as e:
        logger.error(e)
        return False

    for blk in req.iter_content(SAVE_URL_CHUNK_SIZE):
        if isinstance(fd_out, int):
            os.write(fd_out, blk)
        else:
            fd_out.write(blk)

    return True


def save_url_to_dir(url_base, save_dir, file_name, quiet_404=True):
    saved = False
    url_str = os.path.join(url_base, file_name)
    file_path = os.path.join(save_dir, file_name)

    with open(file_path, 'wb') as fd:
        saved = save_url_to_fd(url_str, fd, quiet_404=quiet_404)

    if not saved:
        os.unlink(file_path)

    return saved
