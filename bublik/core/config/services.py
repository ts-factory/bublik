# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib

from django.core.exceptions import ObjectDoesNotExist

from bublik.core.cache import GlobalConfigCache
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
    def get_global_content_from_cache(config_name, project_id):
        config_cache = GlobalConfigCache(config_name, project_id)
        config_content = config_cache.content
        if config_content is None:
            try:
                config_content = Config.objects.get_global(config_name, project_id).content
            except ObjectDoesNotExist:
                config_content = {}
            config_cache.content = config_content
        return config_content

    @staticmethod
    def getattr_from_global(config_name, data_key, project_id, **kwargs):
        with contextlib.suppress(KeyError):
            return ConfigServices.get_global_content_from_cache(config_name, project_id)[
                data_key
            ]
        with contextlib.suppress(KeyError):
            return ConfigServices.get_global_content_from_cache(config_name, None)[data_key]
        if 'default' in kwargs:
            return kwargs['default']

        empties = {
            'string': '',
            'number': 0,
            'integer': 0,
            'boolean': False,
            'array': [],
            'object': {},
            'null': None,
        }
        data_key_settings = ConfigServices.get_schema(ConfigTypes.GLOBAL, config_name).get(
            'properties',
            {},
        )[data_key]
        return data_key_settings.get('default', empties[data_key_settings['type']])
