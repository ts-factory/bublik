# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.signals import post_save
from django.dispatch import receiver

from bublik.core.config.services import ConfigServices
from bublik.data.models import Config, ConfigTypes, GlobalConfigs


@receiver(post_save, sender=Config)
def invalidate_config_cache(sender, instance, **kwargs):
    if (
        instance.type == ConfigTypes.GLOBAL
        and instance.name == GlobalConfigs.PER_CONF.name
        and instance.is_active
    ):
        caches['config'].delete('content')


def get_config_from_cache(default=None):
    config = caches['config'].get('content')
    if config is None:
        try:
            config = Config.objects.get_global(GlobalConfigs.PER_CONF.name).content
            caches['config'].set('content', config, timeout=86400)
        except ObjectDoesNotExist:
            config = default
    return config


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
        config = get_config_from_cache()

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
