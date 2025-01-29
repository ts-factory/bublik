# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import configparser
from datetime import datetime
from itertools import chain
import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from bublik.core.cache import set_tags_categories_cache
from bublik.data.models import (
    Config,
    GlobalConfigNames,
    Meta,
    MetaCategory,
    MetaPattern,
)


logger = logging.getLogger('bublik.server')


class Command(BaseCommand):
    """
    Example of file handled by the command:

    ; Tags from the "host" category have priority 3
    [category-host]
    category = host
    set-priority = 3
    set-comment =
      A multiline
      comment

    ; The "elrond" tag belongs to the "host" category
    [tag-elrond]
    name = elrond
    set-category = host

    ; Tags we don't want to see on the site
    [category-irrelevant]
    category = irrelevant
    set-priority = 10

    ; Tags hinting that a feature was disabled aren't necessary
    [tags-no]
    name = no-.+
    set-category = irrelevant
    """

    help = 'Assign a class and a priority to the tags in the database'

    DEFAULT_TAG_PRIORITY = 4

    def add_arguments(self, parser):
        parser.add_argument(
            'config_names',
            nargs='?',
            default=[
                GlobalConfigNames.META,
                GlobalConfigNames.TAGS,
            ],
            help='Names of the map configs',
        )

    def __resolve_mapping(self, configs_content):
        mapping = {'categories': {}, 'patterns': []}

        for item in configs_content:
            logger.debug(f'item: {item}')

            type = item.get('type', None)
            category = item.get('category', None)
            name = item.get('name', None)
            set_priority = item.get('set-priority', None)
            set_comment = item.get('set-comment', None)
            set_category = item.get('set-category', None)
            set_pattern = item.get('set-pattern', None)

            logger.debug(f'type: {type}')
            logger.debug(f'category: {category}')
            logger.debug(f'name: {name}')
            logger.debug(f'set_priority: {set_priority}')
            logger.debug(f'set_category: {set_category}')
            logger.debug(f'set_comment: {set_comment}')
            logger.debug(f'set_pattern: {set_pattern}')

            pattern = None

            if set_pattern is not None:
                pattern = f'{set_pattern}'

            if name:
                pattern = f'{name}$'

            if set_comment:
                set_comment = set_comment.strip()

            if category and not set_priority:
                set_priority = self.DEFAULT_TAG_PRIORITY
                logger.info(
                    f'no priority is specified for {item}, '
                    f'defaulting to {self.DEFAULT_TAG_PRIORITY}',
                )

            if category and type:
                if category in mapping['categories']:
                    msg = f'the category has already been declared: {category}'
                    raise configparser.Error(
                        msg,
                    )
                logging.debug(f'adding as a category: {category}')
                mapping['categories'][category] = MetaCategory.objects.create(
                    name=category,
                    priority=set_priority,
                    comment=set_comment,
                    type=type,
                )
                if set_pattern:
                    logging.debug(f'adding as a pattern: {pattern}')
                    mapping['patterns'].append((pattern, category))
            if name:
                logging.debug(f'adding as a pattern: {name}')
                mapping['patterns'].append((pattern, set_category))

        return mapping

    @transaction.atomic
    def __apply_mapping(self, mapping):
        for category in mapping['categories'].values():
            logger.debug(f'creating category: {category.name}')
            category.save()

        for pattern, set_category in mapping['patterns']:
            MetaPattern.objects.create(
                pattern=pattern,
                category=mapping['categories'][set_category],
            )
            type = mapping['categories'][set_category].type

            for meta in Meta.objects.filter(type=type, name__regex=pattern):
                mapping['categories'][set_category].metas.add(meta)

            logger.debug(f'linking pattern to its related category: {pattern} - {set_category}')

    def handle(self, *args, **options):
        start_time = datetime.now()
        logger.info('removing all the already existing priority mappings in the database')
        MetaCategory.objects.all().delete()
        MetaPattern.objects.all().delete()

        mapping = None
        try:
            config_names = options['config_names']
            logger.debug(f'reading configs: {config_names}')
            configs_content = list(
                chain(
                    *[
                        Config.objects.get_global(config_name).content
                        for config_name in config_names
                    ],
                ),
            )
            mapping = self.__resolve_mapping(configs_content)
        except configparser.ParsingError as e:
            logger.critical(f'unable to parse config content: {e}')
        else:
            self.__apply_mapping(mapping)
            set_tags_categories_cache()
            logger.debug('tags categories cache was updated')
        finally:
            logger.debug(f'completed in [{datetime.now() - start_time}]')
