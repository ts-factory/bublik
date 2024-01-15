# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import configparser

from datetime import datetime
import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from bublik.core.cache import set_tags_categories_cache
from bublik.data.models import Meta, MetaCategory, MetaPattern


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
            'map_files',
            metavar='path_map',
            nargs='?',
            default=[
                os.path.join(settings.PER_CONF_DIR, 'tags.conf'),
                os.path.join(settings.PER_CONF_DIR, 'meta.conf'),
            ],
            help='Path to a map file, ignored if unexistent',
        )

    def __resolve_mapping(self, h):
        mapping = {'categories': {}, 'patterns': []}
        for section in h.sections():  # why if h.sections() is empty
            logger.debug(f'section: {section}')

            type = h.get(section, 'type', raw=True, fallback=None)
            category = h.get(section, 'category', raw=True, fallback=None)
            name = h.get(section, 'name', raw=True, fallback=None)
            set_priority = h.getint(section, 'set-priority', raw=True, fallback=None)
            set_comment = h.get(section, 'set-comment', raw=True, fallback=None)
            set_category = h.get(section, 'set-category', raw=True, fallback=None)
            set_pattern = h.get(section, 'set-pattern', raw=True, fallback=None)

            logger.debug(f'type: {type}')
            logger.debug(f'category: {category}')
            logger.debug(f'name: {name}')
            logger.debug(f'set_priority: {set_priority}')
            logger.debug(f'set_category: {set_category}')
            logger.debug(f'set_comment: {set_comment}')
            logger.debug(f'set_pattern: {set_pattern}')

            pattern = None

            min_priority = 1
            max_priority = 10

            if set_pattern is not None:
                pattern = f'{set_pattern}'

            if name:
                pattern = f'{name}$'

            if set_comment:
                set_comment = set_comment.strip()

            if category and not set_priority:
                set_priority = self.DEFAULT_TAG_PRIORITY
                logger.info(
                    f'no priority is specified for {section}, '
                    f'defaulting to {self.DEFAULT_TAG_PRIORITY}',
                )

            if set_priority and (set_priority < min_priority or set_priority > max_priority):
                msg = f'priority in invalid [1-10] range: {set_priority}'
                raise configparser.Error(msg)

            if name and category:
                msg = (
                    'name and category will not filter tags together, skipping '
                    '(use one or the other)'
                )
                raise configparser.Error(
                    msg,
                )
            if set_pattern and name:
                msg = 'set-pattern cannot be used in a pattern block'
                raise configparser.Error(msg)
            if set_pattern and set_category:
                msg = 'set-pattern cannot be used in a pattern block'
                raise configparser.Error(msg)
            if category and set_category:
                msg = 'category and set-category cannot be used together'
                raise configparser.Error(msg)
            if not name and not category:
                msg = 'neither name nor category where specified, skipping entry'
                raise configparser.Error(
                    msg,
                )
            if name and not set_category:
                msg = "a pattern was set on a tag, but it wasn't assigned to a category"
                raise configparser.Error(
                    msg,
                )
            if not name and set_category:
                msg = "a category was set on a tag, but it wasn't assigned a name pattern"
                raise configparser.Error(
                    msg,
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

        h = configparser.ConfigParser()

        mapping = None
        try:
            map_files = options['map_files']
            logger.debug(f'reading files: {map_files}')
            files = map_files
            parsed_files = h.read(files)
            ignored_files = set(files) ^ set(parsed_files)
            if ignored_files:
                # If a file named in files cannot be opened, that file will be ignored.
                # This is designed so that you can specify potential
                # configuration file locations
                #
                msg = f'Failed to open/find specified file(s): {options["map_files"]}'
                raise configparser.Error(
                    msg,
                )
            mapping = self.__resolve_mapping(h)
        except configparser.ParsingError as e:
            logger.critical(f'unable to parse file: {e}')
        else:
            self.__apply_mapping(mapping)
            set_tags_categories_cache()
            logger.debug('tags categories cache was updated')
        finally:
            logger.debug(f'completed in [{datetime.now() - start_time}]')
