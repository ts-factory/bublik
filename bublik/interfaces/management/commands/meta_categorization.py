# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
from itertools import chain
import logging

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand
from django.db import transaction

from bublik.core.cache import set_tags_categories_cache
from bublik.data.models import (
    Config,
    GlobalConfigs,
    Meta,
    MetaCategory,
    MetaPattern,
)


logger = logging.getLogger('bublik.server')


class Command(BaseCommand):
    '''
    Example of config content handled by the command:
    [
        {
            "type": "label",
            "category": "Configuration",
            "set-comment": "Label with category Configuration",
            "set-patterns": ["CFG"]
        },
        {
            "type": "tag",
            "category": "linux",
            "set-comment": "Linux major-minor version",
            "set-patterns": ["linux-mm$"],
            "set-priority": 3
        },
        {
            "type": "tag",
            "category": "irrelevant_tag",
            "set-priority": 10
        }
    ]
    '''

    help = 'Assign a class and a priority to the metas in the database'

    DEFAULT_META_PRIORITY = 4

    def add_arguments(self, parser):
        parser.add_argument(
            'config_names',
            nargs='?',
            default=[
                GlobalConfigs.META.name,
            ],
            help='Names of the map configs',
        )

    @transaction.atomic
    def create_and_assign_meta_categories(self, configs_content):
        for item in configs_content:
            logger.debug(f'item: {item}')

            category = item['category']
            type = item['type']
            priority = item.get('set-priority', None)
            comment = item.get('set-comment', None)
            patterns = item.get('set-patterns', [])

            logger.debug(f'category: {category}')
            logger.debug(f'type: {type}')
            logger.debug(f'set_priority: {priority}')
            logger.debug(f'set_comment: {comment}')
            logger.debug(f'set_patterns: {patterns}')

            if comment:
                comment = comment.strip()

            if not priority:
                priority = self.DEFAULT_META_PRIORITY
                logger.info(
                    f'no priority is specified for {item}, '
                    f'defaulting to {self.DEFAULT_META_PRIORITY}',
                )

            logger.debug(f'creating category: {category}')
            category_obj = MetaCategory.objects.create(
                name=category,
                priority=priority,
                comment=comment,
                type=type,
            )

            for pattern in patterns:
                logger.debug(f'creating category pattern: {pattern}')
                MetaPattern.objects.create(
                    pattern=pattern,
                    category=category_obj,
                )

                for meta in Meta.objects.filter(type=type, name__regex=pattern):
                    category_obj.metas.add(meta)

    def handle(self, *args, **options):
        start_time = datetime.now()

        logger.info(
            'removing all meta categories',
        )
        MetaCategory.objects.all().delete()

        config_names = options['config_names']

        logger.debug(
            f'retrieving configs: {config_names}',
        )
        configs_content = []
        for config_name in config_names:
            try:
                config = Config.objects.get_global(config_name)
                configs_content.append(config.content)
            except ObjectDoesNotExist:
                logger.warning(
                    f'{config_name} configuration object doesn\'t exist',
                )
        configs_content = list(chain(*configs_content))

        if not configs_content:
            logger.warning(
                'meta categorization skipped due to empty configuration content',
            )
        else:
            try:
                logger.debug(
                    'create and assign meta categories',
                )
                self.create_and_assign_meta_categories(configs_content)
            except Exception as e:
                logger.critical(f'failed to create and assign meta categories: {e}')
            else:
                set_tags_categories_cache()
                logger.debug('tags categories cache was updated')

        logger.debug(f'completed in [{datetime.now() - start_time}]')
