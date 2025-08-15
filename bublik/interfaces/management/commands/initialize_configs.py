# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand

from bublik.core.config.services import ConfigServices
from bublik.data.models import (
    Config,
    ConfigTypes,
    GlobalConfigs,
)
from bublik.data.schemas.services import generate_content
from bublik.data.serializers import ConfigSerializer


class Command(BaseCommand):
    def handle(self, *args, **options):
        '''
        Initialize required global configurations according to JSON schemas.
        '''

        self.stdout.write('Initialize required configurations if they are not exist:')
        for config in GlobalConfigs.required():
            try:
                Config.objects.get_global(config.name)
                self.stdout.write(f'{config}: already exist!')
            except ObjectDoesNotExist:
                json_schema = ConfigServices.get_schema(
                    ConfigTypes.GLOBAL,
                    config.name,
                )
                ConfigSerializer.initialize(
                    {
                        'type': ConfigTypes.GLOBAL,
                        'name': config.name,
                        'description': config.description,
                        'content': generate_content(json_schema),
                    },
                )
                self.stdout.write(
                    self.style.SUCCESS(f'{config}: succesfully initialized!'),
                )
