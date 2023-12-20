# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.cache import caches
from django.core.management.base import BaseCommand


UNUSED_CACHE_KEYS = ['dashboard', 'dashboard-next']


class Command(BaseCommand):
    def handle(self, *args, **options):
        try:
            for key in UNUSED_CACHE_KEYS:
                caches['run'].delete_pattern(f'*.{key}')
            msg = self.style.SUCCESS(
                f'Cache keys {UNUSED_CACHE_KEYS} were successfully deleted!',
            )
        except Exception as e:
            msg = self.style.ERROR(str(e))
        finally:
            self.stdout.write(msg)
