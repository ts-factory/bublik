# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
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
        """
        Initialize default required configurations from JSON schemas.
        """

        self.stdout.write('Initialize default required configurations if they do not exist:')
        configs = GlobalConfigs.required()
        if settings.AI_CHAT_ENABLED:
            # The `ai` config is not required in general, but chat is unusable
            # without it: a missing config reads back as an empty provider set
            # rather than falling back to the schema default. Seed it from that
            # default so there is something to edit once chat is enabled.
            configs = [*configs, GlobalConfigs.AI]
        for config in configs:
            try:
                Config.objects.get_global(config.name, None)
                self.stdout.write(f'Default {config}: already exist!')
            except ObjectDoesNotExist:
                json_schema = ConfigServices.get_schema(
                    ConfigTypes.GLOBAL,
                    config.name,
                )
                ConfigSerializer.initialize(
                    {
                        'type': ConfigTypes.GLOBAL,
                        'name': config.name,
                        'project': None,
                        'description': config.description,
                        'content': generate_content(json_schema),
                    },
                )
                self.stdout.write(
                    self.style.SUCCESS(f'Default {config}: succesfully initialized!'),
                )
