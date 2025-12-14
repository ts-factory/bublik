# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from django.core.management.base import BaseCommand, CommandError

from bublik.core.argparse import parser_type_int_or_none
from bublik.core.cache import ProjectCache
from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs, Project


class Command(BaseCommand):
    help = 'Delete, create, or update cached project data.'
    PROJECT_SECTION_CHOICES: ClassVar[set] = {
        'configs',
        'tags',
    }

    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            choices=['delete', 'create', 'update'],
            type=str,
            help='Apply specified action to cached project data',
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
            '-d',
            '--data',
            type=str,
            nargs='+',
            choices=self.PROJECT_SECTION_CHOICES,
            default=self.PROJECT_SECTION_CHOICES,
            help='Project chached data keys',
        )

    def clear_cache(self, project_ids, data_keys):
        for pid in project_ids:
            for data_key in data_keys:
                if data_key == 'configs':
                    ProjectCache(pid).configs.clear_all()
                elif data_key == 'tags':
                    ProjectCache(pid).tags.clear_all()

    def load_cache(self, project_ids, data_keys):
        for data_key in data_keys:
            if data_key == 'configs':
                configs_name_project = [
                    (cfg_name, pid) for cfg_name in GlobalConfigs.all() for pid in project_ids
                ]
                for config_name_project in configs_name_project:
                    ConfigServices.get_global_content_from_cache(*config_name_project)
            elif data_key == 'tags':
                for pid in project_ids:
                    ProjectCache(pid).tags.load()

    def handle(self, *args, **options):
        try:
            action = options['action']
            project_ids = options['project']
            data_keys = options['data']

            if action != 'create':
                self.clear_cache(project_ids, data_keys)
            if action != 'delete':
                self.load_cache(project_ids, data_keys)

            project_ids_str = ', '.join(map(str, project_ids))
            for data_key in data_keys:
                self.stdout.write(
                    self.style.SUCCESS(
                        f'{data_key.capitalize()} cache successfully {action}d '
                        f'for projects: {project_ids_str}',
                    ),
                )

        except Exception as e:
            raise CommandError(e) from CommandError
