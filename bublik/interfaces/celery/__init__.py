# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os

from celery import Celery


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')

# This will make sure the app is always imported when
# Django starts so that shared_task will use this app.
app = Celery('interfaces')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

__all__ = ['app']
