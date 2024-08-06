# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import logging

import per_conf

from bublik.core.checks import check_per_conf
from bublik.data.models import Config


logger = logging.getLogger('bublik.server')


def getattr_from_per_conf(data_key, default=None, required=False):
    per_conf_obj = Config.get_current_version('global', 'per_conf')
    if not per_conf_obj:
        logger.warning(
            'You need to move the contents of per_conf.py to the DB by creating '
            'a global per_conf config object',
        )
        if required and not check_per_conf(data_key, logger):
            raise AttributeError
        return getattr(per_conf, data_key, default)
    if data_key in per_conf_obj.content:
        return per_conf_obj.content[data_key]
    if required:
        msg = f"'{data_key}' wasn\'t found in per_conf global config object"
        raise KeyError(msg)
    return default
