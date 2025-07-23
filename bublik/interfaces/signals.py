# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.cache import caches
from django.core.management import call_command
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from bublik.core.cache import RunCache
from bublik.data.models import (
    Config,
    ConfigTypes,
    GlobalConfigs,
    Meta,
    MetaTest,
    TestIterationResult,
)


@receiver(pre_delete)
def delete_cache(instance, sender, **kwargs):
    if sender == TestIterationResult and instance.test_run is None:
        RunCache.delete_data_for_obj(instance)


@receiver(post_save, sender=Config)
def invalidate_config_cache(sender, instance, **kwargs):
    if (
        instance.type == ConfigTypes.GLOBAL
        and instance.name == GlobalConfigs.PER_CONF.name
        and instance.is_active
    ):
        caches['config'].delete('content')


@receiver(post_save, sender=Config)
def categorize_metas_on_config_change(sender, instance, **kwargs):
    if (
        instance.type == ConfigTypes.GLOBAL
        and instance.name == GlobalConfigs.META.name
        and instance.is_active
    ):
        call_command('meta_categorization')


@receiver(post_delete, sender=MetaTest)
def delete_orphan_meta(sender, instance, **kwargs):
    if not MetaTest.objects.filter(meta_id=instance.meta_id).exists():
        Meta.objects.filter(id=instance.meta_id).delete()
