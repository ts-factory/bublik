# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.


from django.conf import settings

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs


class DynamicSettingsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        project_id = request.GET.get('project', None)

        def get_setting(attr):
            return ConfigServices.getattr_from_global(
                GlobalConfigs.PER_CONF.name,
                attr,
                project_id,
            )

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
