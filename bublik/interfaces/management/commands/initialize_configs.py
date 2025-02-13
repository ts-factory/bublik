# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ObjectDoesNotExist
from django.core.management import call_command
from django.core.management.base import BaseCommand

from bublik.core.config.services import ConfigServices
from bublik.data.models import (
    Config,
    ConfigTypes,
    GlobalConfigNames,
)
from bublik.data.schemas.services import generate_content
from bublik.data.serializers import ConfigSerializer


class Command(BaseCommand):
    def handle(self, *args, **options):
        '''
        Initialize required global configurations according to JSON schemas.
        '''
        call_command(
            'migrate_configs',
        )

        self.stdout.write('\nInitialize required configurations if they are not exist:')
        for config_name in GlobalConfigNames.required():
            try:
                Config.objects.get_global(config_name)
                self.stdout.write(f'{config_name}: already exist!')
            except ObjectDoesNotExist:
                json_schema = ConfigServices.get_schema(
                    ConfigTypes.GLOBAL,
                    config_name,
                )
                ConfigSerializer.initialize(
                    {
                        'type': ConfigTypes.GLOBAL,
                        'name': config_name,
                        'content': generate_content(json_schema),
                    },
                )
                self.stdout.write(
                    self.style.SUCCESS(f'{config_name}: succesfully initialized!'),
                )
