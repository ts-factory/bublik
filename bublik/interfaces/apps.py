# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.apps import AppConfig


class InterfacesConfig(AppConfig):
    name = 'bublik.interfaces'

    def ready(self):
        import bublik.interfaces.signals
