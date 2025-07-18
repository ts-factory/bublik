# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.


from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models.signals import post_save

from bublik.data.models import Config, ConfigTypes, GlobalConfigs
from bublik.interfaces.signals import categorize_metas_on_config_change, signal_disabled


def check_duplicates(config):
    categories = [item.get('category') for item in config.content if item.get('category')]
    duplicates = {c for c in categories if categories.count(c) > 1}
    if duplicates:
        config_label = f'{config.name} ({config.type}, v{config.version})'
        msg = (
            f'Duplicate category values found in {config_label} configuration: {duplicates}. '
            'Please resolve these conflicts manually before proceeding.'
        )
        raise ValueError(msg)


class Command(BaseCommand):

    @transaction.atomic()
    def handle(self, *args, **options):

        self.stdout.write(
            'Merging active versions of meta and tags configurations into meta...',
        )

        try:
            self.stdout.write(
                'Retrieving and validating active or latest versions of meta and tags...',
            )

            tags = (
                Config.objects.filter(type=ConfigTypes.GLOBAL, name='tags')
                .order_by('-is_active', '-created')
                .first()
            )
            if tags:
                check_duplicates(tags)

            meta = (
                Config.objects.filter(type=ConfigTypes.GLOBAL, name=GlobalConfigs.META.name)
                .order_by('-is_active', '-created')
                .first()
            )
            if meta:
                check_duplicates(meta)

            if not tags:
                self.stdout.write('No tags configurations found — skipping merge.')
                return
            if not meta:
                with signal_disabled(
                    post_save,
                    categorize_metas_on_config_change,
                    sender=Config,
                ):
                    Config.objects.filter(type=ConfigTypes.GLOBAL, name='tags').update(
                        name=GlobalConfigs.META.name,
                    )
                self.stdout.write(
                    self.style.SUCCESS('Only tags configurations found. Renamed to meta.'),
                )
                return

            meta_categories = {item.get('category') for item in meta.content}
            tags_categories = {item.get('category') for item in tags.content}
            duplicates = meta_categories & tags_categories
            if duplicates:
                meta_label = f'{meta.name} ({meta.type}, v{meta.version})'
                tags_label = f'{tags.name} ({tags.type}, v{tags.version})'
                msg = (
                    f'Duplicate category values found between {meta_label} and {tags_label} '
                    f'configurations: {duplicates}. Please resolve these conflicts '
                    'manually before proceeding.'
                )
                raise ValueError(msg)

            merged_content = [*meta.content, *tags.content]
            meta.content = merged_content
            with signal_disabled(post_save, categorize_metas_on_config_change, sender=Config):
                meta.save()
            self.stdout.write(
                self.style.SUCCESS(
                    'Meta and tags configurations successfully merged into meta.',
                ),
            )

            Config.objects.filter(type=ConfigTypes.GLOBAL, name='tags').delete()
            self.stdout.write(self.style.SUCCESS('Tags configurations successfully deleted.'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(e))
