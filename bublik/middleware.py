# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ObjectDoesNotExist
from django.db.models.signals import post_save
from django.dispatch import receiver

from bublik.data.models import Config, ConfigTypes, GlobalConfigNames


@receiver(post_save, sender=Config)
def invalidate_config_cache(sender, instance, **kwargs):
    if (
        instance.type == ConfigTypes.GLOBAL
        and instance.name == GlobalConfigNames.PER_CONF
        and instance.is_active
    ):
        caches['config'].delete('content')


def get_config_from_cache(default=None):
    config = caches['config'].get('content')
    if config is None:
        try:
            config = Config.objects.get(
                type=ConfigTypes.GLOBAL,
                name=GlobalConfigNames.PER_CONF,
                is_active=True,
            ).content
            caches['config'].set('content', config, timeout=86400)
        except ObjectDoesNotExist:
            config = default
    return config


class DynamicSettingsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        config = get_config_from_cache()

        # set CSRF_TRUSTED_ORIGINS
        csrf_trusted_origins = config.get('CSRF_TRUSTED_ORIGINS', [])
        settings.CSRF_TRUSTED_ORIGINS = csrf_trusted_origins

        # set UI_PREFIX
        ui_version = config.get('UI_VERSION', 2)
        ui_prefix_by_ui_version = {
            2: 'v2',
        }
        ui_prefix = ui_prefix_by_ui_version.get(ui_version, '')
        settings.UI_PREFIX = ui_prefix

        return self.get_response(request)
