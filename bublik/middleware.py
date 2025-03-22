# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ObjectDoesNotExist

from bublik.core.config.services import ConfigServices
from bublik.core.utils import convert_to_int_if_digit
from bublik.data.models import Config, ConfigTypes, GlobalConfigs


def get_config_from_cache(project_id, default=None):
    config_content = caches['config'].get('content')
    config_project = caches['config'].get('project')
    if config_project != convert_to_int_if_digit(project_id) or config_content is None:
        try:
            config = Config.objects.get_global(GlobalConfigs.PER_CONF.name, project_id)
            caches['config'].set('content', config.content, timeout=86400)
            caches['config'].set(
                'project',
                project_id,
                timeout=86400,
            )
        except ObjectDoesNotExist:
            config_content = default
    return config_content


def get_schema_from_cache():
    config_schema = caches['config'].get('schema')
    if config_schema is None:
        config_schema = ConfigServices.get_schema(
            ConfigTypes.GLOBAL,
            GlobalConfigs.PER_CONF.name,
        )
        caches['config'].set('schema', config_schema, timeout=86400)
    return config_schema


class DynamicSettingsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        project_id = request.GET.get('project', None)
        config = get_config_from_cache(project_id)

        def get_setting(attr):
            if config and attr in config:
                return config.get(attr)
            return get_schema_from_cache()['properties'][attr]['default']

        # set CSRF_TRUSTED_ORIGINS
        settings.CSRF_TRUSTED_ORIGINS = get_setting('CSRF_TRUSTED_ORIGINS')

        # set UI_PREFIX
        ui_version = get_setting('UI_VERSION')
        ui_prefix_by_ui_version = {
            2: 'v2',
        }
        ui_prefix = ui_prefix_by_ui_version.get(ui_version, '')
        settings.UI_PREFIX = ui_prefix

        # set email settings
        settings.EMAIL_PORT = get_setting('EMAIL_PORT')
        settings.EMAIL_HOST = get_setting('EMAIL_HOST')
        settings.EMAIL_USE_TLS = get_setting('EMAIL_USE_TLS')
        settings.EMAIL_TIMEOUT = get_setting('EMAIL_TIMEOUT')
        settings.EMAIL_FROM = get_setting('EMAIL_FROM')
        settings.EMAIL_ADMINS = get_setting('EMAIL_ADMINS')

        return self.get_response(request)
