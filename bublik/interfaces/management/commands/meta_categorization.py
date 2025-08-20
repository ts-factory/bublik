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
    Project,
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
            '-prj',
            '--project',
            type=str,
            default=None,
            choices=[*Project.objects.values_list('name', flat=True), None],
        )

    @transaction.atomic
    def create_and_assign_meta_categories(self, configs_content_per_project):
        categories = set()
        for pid, configs_content in configs_content_per_project.items():
            for item in configs_content:
                logger.debug(f'[project id={pid}] item: {item}')

                category = item['category']
                if category in categories:
                    continue
                categories.add(category)

                type = item['type']
                priority = item.get('set-priority', None)
                comment = item.get('set-comment', None)
                patterns = item.get('set-patterns', [])

                logger.debug(f'[project id={pid}] category: {category}')
                logger.debug(f'[project id={pid}] type: {type}')
                logger.debug(f'[project id={pid}] set_priority: {priority}')
                logger.debug(f'[project id={pid}] set_comment: {comment}')
                logger.debug(f'[project id={pid}] set_patterns: {patterns}')

                if comment:
                    comment = comment.strip()

                if not priority:
                    priority = self.DEFAULT_META_PRIORITY
                    logger.info(
                        f'[project id={pid}] no priority is specified for {item}, '
                        f'defaulting to {self.DEFAULT_META_PRIORITY}',
                    )

                logger.debug(f'[project id={pid}] creating category: {category}')
                category_obj = MetaCategory.objects.create(
                    name=category,
                    priority=priority,
                    comment=comment,
                    type=type,
                    project_id=pid,
                )

                for pattern in patterns:
                    logger.debug(f'[project id={pid}] creating category pattern: {pattern}')
                    MetaPattern.objects.create(
                        pattern=pattern,
                        category=category_obj,
                    )

                    for meta in Meta.objects.filter(type=type, name__regex=pattern):
                        category_obj.metas.add(meta)

    def handle(self, *args, **options):
        start_time = datetime.now()

        config_name = GlobalConfigs.META.name

        project_name = options['project']
        project = Project.objects.get(name=project_name) if project_name else None
        project_id = project.id if project else None

        logger.info(
            f'[project id={project_id}] removing all meta categories',
        )
        MetaCategory.objects.filter(project_id=project_id).delete()

        project_ids = [project_id, None] if project_id else [None]

        logger.debug(
            f'[project id={project_id}] retrieving config: {config_name}',
        )
        configs_content_per_project = {}
        for pid in project_ids:
            configs_content_per_project[pid] = []
            try:
                config = Config.objects.get_global(config_name, pid)
                configs_content_per_project[pid].append(config.content)
            except ObjectDoesNotExist:
                logger.warning(
                    f'[project id={pid}]: {config_name} configuration object doesn\'t exist',
                )
            configs_content_per_project[pid] = list(chain(*configs_content_per_project[pid]))

        if not any(configs_content_per_project.values()):
            logger.warning(
                'meta categorization skipped due to empty configuration content',
            )
        else:
            try:
                logger.debug(
                    'create and assign meta categories',
                )
                self.create_and_assign_meta_categories(configs_content_per_project)
            except Exception as e:
                logger.critical(f'failed to create and assign meta categories: {e}')
            else:
                set_tags_categories_cache(project_id)
                logger.debug('tags categories cache was updated')

        logger.debug(f'completed in [{datetime.now() - start_time}]')
