# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib

from django.core.exceptions import ObjectDoesNotExist

from bublik.data.models import Config, ConfigTypes, GlobalConfigs
from bublik.data.schemas.services import load_schema


class ConfigServices:
    @staticmethod
    def get_schema(config_type, config_name):
        if config_type == ConfigTypes.REPORT:
            return load_schema('report')
        if config_type == ConfigTypes.SCHEDULE:
            return load_schema('schedule')
        if config_type == ConfigTypes.GLOBAL:
            if config_name == GlobalConfigs.PER_CONF.name:
                return load_schema('per_conf')
            if config_name == GlobalConfigs.REFERENCES.name:
                return load_schema('references')
            if config_name == GlobalConfigs.META.name:
                return load_schema('meta')
        return None

    @staticmethod
    def getattr_from_global(config_name, data_key, project_id, **kwargs):
        with contextlib.suppress(ObjectDoesNotExist, KeyError):
            return Config.objects.get_global(config_name, project_id).content[data_key]
        with contextlib.suppress(ObjectDoesNotExist, KeyError):
            return Config.objects.get_global(config_name, None).content[data_key]
        if 'default' in kwargs:
            return kwargs['default']
        return None
