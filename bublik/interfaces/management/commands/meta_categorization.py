# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
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
    def create_and_assign_meta_categories(self, meta_categories, project_id):
        for item in meta_categories:
            logger.debug(f'[project id={project_id}] item: {item}')

            category = item['category']
            type = item['type']
            priority = item.get('set-priority', None)
            comment = item.get('set-comment', None)
            patterns = item.get('set-patterns', [])

            logger.debug(f'[project id={project_id}] category: {category}')
            logger.debug(f'[project id={project_id}] type: {type}')
            logger.debug(f'[project id={project_id}] set_priority: {priority}')
            logger.debug(f'[project id={project_id}] set_comment: {comment}')
            logger.debug(f'[project id={project_id}] set_patterns: {patterns}')

            if comment:
                comment = comment.strip()

            if not priority:
                priority = self.DEFAULT_META_PRIORITY
                logger.info(
                    f'[project id={project_id}] no priority is specified for {item}, '
                    f'defaulting to {self.DEFAULT_META_PRIORITY}',
                )

            logger.debug(f'[project id={project_id}] creating category: {category}')
            category_obj = MetaCategory.objects.create(
                name=category,
                priority=priority,
                comment=comment,
                type=type,
                project_id=project_id,
            )

            for pattern in patterns:
                logger.debug(f'[project id={project_id}] creating category pattern: {pattern}')
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

        # Delete the categories created from the configuration that belong
        # to the specified project; if no project is specified, delete all categories.
        meta_categories_to_delete = MetaCategory.objects.all()
        msg = 'removing all meta categories'
        if project_id:
            meta_categories_to_delete = meta_categories_to_delete.filter(project_id=project_id)
            msg += f' associated with the {project_name} project'
        logger.info(msg)
        meta_categories_to_delete.delete()

        # When running categorization with the default configuration,
        # categories need to be updated for all projects, since the default categories
        # apply to each project.
        project_ids = (
            [project_id]
            if project_id
            else [None, *Project.objects.values_list('id', flat=True)]
        )

        def get_meta_content(pid):
            try:
                content = Config.objects.get_global(config_name, pid).content
                if not content:
                    logger.warning(
                        f'[project id={pid}] {config_name} configuration is empty',
                    )
                    return []
                return content
            except ObjectDoesNotExist:
                logger.warning(
                    f'[project id={pid}] {config_name} configuration is missing',
                )
                return []

        meta_categories = get_meta_content(None)

        for pid in project_ids:
            logger.debug(f'[project id={pid}] retrieving categories from config')
            # Retrieve the categories for a project as a combination
            # of the default categories and the project-specific categories.
            if pid is not None:
                meta_categories = list(
                    {
                        **{item['category']: item for item in meta_categories},
                        **{item['category']: item for item in get_meta_content(pid)},
                    }.values(),
                )

            # Create and assign meta categories for the project
            if not meta_categories:
                logger.warning(
                    f'[project id={pid}] meta categorization skipped due to empty '
                    'configuration content',
                )
            else:
                try:
                    logger.debug(
                        f'[project id={pid}] create and assign meta categories',
                    )
                    self.create_and_assign_meta_categories(meta_categories, pid)
                except Exception as e:
                    logger.critical(
                        f'[project id={pid}] failed to create and assign meta categories: {e}',
                    )
                else:
                    set_tags_categories_cache(pid)
                    logger.debug(f'[project id={pid}] tags categories cache was updated')

        logger.debug(f'completed in [{datetime.now() - start_time}]')
