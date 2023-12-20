# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from bublik.core.cache import RunCache
from bublik.data import models


@receiver(pre_delete)
def delete_cache(instance, sender, **kwargs):
    if sender == models.TestIterationResult and instance.test_run is None:
        RunCache.delete_data_for_obj(instance)
