# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

import configparser
import importlib.util
import os

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from bublik.core.utils import convert_to_int_if_digit
from bublik.data.models import (
    Config,
    ConfigTypes,
    GlobalConfigs,
)
from bublik.data.serializers import ConfigSerializer


def any_config_file_exists(config_file_names):
    '''
    Check the existence of the PER_CONF_DIR directory and the presence of passed files in it.
    '''
    config_file_paths = [
        os.path.join(settings.PER_CONF_DIR, config_file_name)
        for config_file_name in config_file_names
    ]
    return settings.PER_CONF_DIR and any(
        os.path.isfile(config_file_path) for config_file_path in config_file_paths
    )


def read_py_file(file_path):
    spec = importlib.util.spec_from_file_location('module_name', file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_conf_file(file_path):
    h = configparser.ConfigParser()
    h.read(file_path)
    return [
        {
            attr_name: convert_to_int_if_digit(
                h.get(section, attr_name, raw=True, fallback=None),
            )
            for attr_name in [
                'type',
                'category',
                'name',
                'set-priority',
                'set-comment',
                'set-category',
                'set-pattern',
            ]
            if h.get(section, attr_name, raw=True, fallback=None)
        }
        for section in h.sections()
    ]


class Command(BaseCommand):
    def migrate_config(self, config_name, config_description, config_content):
        '''
        Create global config object with passed name, description and content.
        '''
        configs = Config.objects.filter(type=ConfigTypes.GLOBAL, name=config_name)
        if configs:
            self.stdout.write(f'{config_name}: already migrated!')
        else:
            # create a configuration object, skipping content validation
            ConfigSerializer.initialize(
                {
                    'type': ConfigTypes.GLOBAL,
                    'name': config_name,
                    'description': config_description,
                    'content': config_content,
                },
            )
            self.stdout.write(self.style.SUCCESS(f'{config_name}: succesfully migrated!'))

            # bring the configuration object content to the current format
            call_command(
                'reformat_configs',
                '-t',
                ConfigTypes.GLOBAL,
                '-n',
                config_name,
            )

    def handle(self, *args, **options):
        self.stdout.write('Migrate configurations from the directory to the database:')

        config_file_names = ['per_conf.py', 'references.py', 'meta.conf', 'tags.conf']

        # check the existence of the PER_CONF_DIR directory and the presence
        # of configurations in it
        if not any_config_file_exists(config_file_names):
            self.stdout.write('Nothing to migrate')
            return

        configs_data = {}

        # read config files
        for config_file_name in config_file_names:
            config_file_path = os.path.join(settings.PER_CONF_DIR, config_file_name)
            if os.path.isfile(config_file_path):
                if config_file_name.endswith('.py'):
                    configs_data[config_file_name] = read_py_file(config_file_path)
                elif config_file_name.endswith('.conf'):
                    configs_data[config_file_name] = read_conf_file(config_file_path)

        # preprocess the config file content and create the corresponding config object
        for config_file_name, content in configs_data.items():
            if config_file_name == 'per_conf.py':
                name, description = (
                    GlobalConfigs.PER_CONF.name,
                    GlobalConfigs.PER_CONF.description,
                )
                content = {
                    key: value
                    for key, value in vars(content).items()
                    if not key.startswith('__')
                }
                if 'RUN_STATUS_BY_NOK_BORDERS' in content:
                    # convert tuple into list
                    content['RUN_STATUS_BY_NOK_BORDERS'] = list(
                        content['RUN_STATUS_BY_NOK_BORDERS'],
                    )
            elif config_file_name == 'references.py':
                name, description = (
                    GlobalConfigs.REFERENCES.name,
                    GlobalConfigs.REFERENCES.description,
                )
                content = {
                    key.upper(): value
                    for key, value in vars(vars(content).get('References')).items()
                    if not key.startswith('__')
                }
            elif config_file_name == 'meta.conf':
                name, description = GlobalConfigs.META.name, GlobalConfigs.META.description
            elif config_file_name == 'tags.conf':
                name, description = (
                    GlobalConfigs.TAGS.name,
                    GlobalConfigs.TAGS.description,
                )
            else:
                continue

            if content:
                self.migrate_config(name, description, content)
