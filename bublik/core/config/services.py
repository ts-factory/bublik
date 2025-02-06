# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import logging

from django.core.exceptions import ObjectDoesNotExist

from bublik.data.models import Config, GlobalConfigNames


logger = logging.getLogger('bublik.server')


def getattr_from_per_conf(data_key, default=None, required=False):
    per_conf_obj = Config.objects.get_global(GlobalConfigNames.PER_CONF)
    if not per_conf_obj:
        msg = (
            'There is no active global per_conf configuration object. '
            'Create one or activate one of the existing ones'
        )
        raise ObjectDoesNotExist(msg)
    if data_key in per_conf_obj.content:
        return per_conf_obj.content[data_key]
    if required:
        msg = f"'{data_key}' wasn't found in per_conf global configuration object"
        raise KeyError(msg)
    return default
