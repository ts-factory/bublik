# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

import sys

from django.core.management.base import BaseCommand, CommandError

from bublik.core.argparse import parser_type_int_or_none
from bublik.core.cache import GlobalConfigCache
from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs, Project


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

            configs_name_project = [
                (cfg_name, pid) for cfg_name in GlobalConfigs.all() for pid in project_ids
            ]

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
