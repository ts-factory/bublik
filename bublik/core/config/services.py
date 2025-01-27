# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from bublik.data.models import Config, ConfigTypes, GlobalConfigNames
from bublik.data.schemas.services import load_schema


class ConfigServices:
    @staticmethod
    def get_schema(config_type, config_name):
        if config_type == ConfigTypes.REPORT:
            return load_schema('report')
        if config_type == ConfigTypes.GLOBAL:
            if config_name == GlobalConfigNames.PER_CONF:
                return load_schema('per_conf')
            if config_name == GlobalConfigNames.REFERENCES:
                return load_schema('references')
            if config_name in [GlobalConfigNames.META, GlobalConfigNames.TAGS]:
                return load_schema('meta')
        return None

    @staticmethod
    def getattr_from_global(config_name, data_key, **kwargs):
        config_content = Config.objects.get_global(config_name).content
        json_schema = ConfigServices.get_schema(ConfigTypes.GLOBAL, config_name)
        if data_key in json_schema.get('required', []) or data_key in config_content:
            return config_content[data_key]
        if 'default' in kwargs:
            return kwargs['default']
        return None
