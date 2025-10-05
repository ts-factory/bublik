# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

import sys

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from bublik.core.argparse import parser_type_int_or_none
from bublik.core.cache import GlobalConfigCache
from bublik.core.config.services import ConfigServices
from bublik.data.models import Config, Project


class Command(BaseCommand):
    help = 'Delete, create, or update cached configs content.'

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['delete', 'create', 'update'],
            type=str,
            help='Apply specified action to cached configs content',
        )
        parser.add_argument(
            '-p',
            '--project',
            type=parser_type_int_or_none,
            nargs='+',
            choices=[None, *Project.objects.all().values_list('id', flat=True)],
            default=[None, *Project.objects.all().values_list('id', flat=True)],
            help='Project IDs',
        )
        parser.add_argument(
            '-n',
            '--name',
            type=str,
            nargs='+',
            choices=GlobalConfigCache.CONFIG_NAME_CHOICES,
            default=GlobalConfigCache.CONFIG_NAME_CHOICES,
            help='Global config names',
        )

    def handle(self, *args, **options):
        try:
            action = options['action']
            project_ids = options['project']
            config_names = options['name']

            configs = Config.objects.filter(
                is_active=True,
                name__in=config_names,
            )
            project_query = Q(project__in=project_ids)
            if None in project_ids:
                project_query |= Q(project__isnull=True)
            configs_name_project = configs.filter(project_query).values_list(
                'name',
                'project_id',
            )

            if not configs_name_project:
                self.stdout.write(
                    self.style.ERROR('Configs by specified parameters were not found!'),
                )
                sys.exit(1)

            if action != 'create':
                for project_id in project_ids:
                    for config_name in config_names:
                        del GlobalConfigCache(config_name, project_id).content
            if action != 'delete':
                for config_name_project in configs_name_project:
                    ConfigServices.get_global_content_from_cache(*config_name_project)

            self.stdout.write(
                self.style.SUCCESS(
                    f'Cache {action}d successfully — project IDs: {project_ids}; '
                    f'configs: {config_names}',
                ),
            )

        except Exception as e:
            raise CommandError(e) from CommandError
