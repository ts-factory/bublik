# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand
from django.db.models import Case, IntegerField, Q, When
from rest_framework import serializers

from bublik.core.config.reformatting.dispatcher import (
    ConfigReformatDispatcher,
    ConfigReformatStatuses,
)
from bublik.data.models import Config, ConfigTypes
from bublik.data.serializers import ConfigSerializer


class Command(BaseCommand):
    def get_configs(self, options):
        '''
        Get a list of configurations filtered by the passed types and names.
        '''
        configs = Config.objects.all().order_by('type', 'name', 'version')
        if not options['type'] and not options['name']:
            return configs

        if not options['name']:
            # we only have valid types as a result of parsing arguments
            return configs.filter(type__in=options['type'])

        if not options['type']:
            # name validation is possible only if there is a type
            return configs.filter(name__in=options['name'])

        types_names = dict.fromkeys(options['type'], options['name'])
        for config_type, config_names in types_names.items():
            valid_config_names = []
            for config_name in config_names:
                serializer = ConfigSerializer(data={'type': config_type, 'name': config_name})
                try:
                    serializer.validate_type(config_type)
                    serializer.validate_name(config_name)
                    valid_config_names.append(config_name)
                except serializers.ValidationError as ve:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Invalid configuration type-name "{config_type}-{config_name}": '
                            f'{type(ve).__name__}: {ve}. Skipped.',
                        ),
                    )
            types_names[config_type] = valid_config_names

        if types_names:
            types_names_filter = Q()
            for config_type, config_names in types_names.items():
                types_names_filter |= Q(type=config_type, name__in=config_names)
            return configs.filter(types_names_filter)

        return None

    def add_arguments(self, parser):
        parser.add_argument(
            '-t',
            '--type',
            type=str,
            choices=ConfigTypes.all(),
            nargs='+',
            help='Types of configurations for reformatting',
        )
        parser.add_argument(
            '-n',
            '--name',
            type=str,
            nargs='+',
            help='Names of configurations for reformatting',
        )

    def handle(self, *args, **options):
        self.stdout.write('Reformat configurations:')
        # get configs
        configs = (
            self.get_configs(options)
            .annotate(
                sort_order=Case(
                    When(type='global', name='per_conf', is_active=True, then=3),
                    When(type='global', name='per_conf', then=2),
                    default=1,
                    output_field=IntegerField(),
                ),
            )
            .order_by('sort_order')
        )
        if not configs:
            self.stdout.write(
                self.style.WARNING(
                    'No valid configurations were found. Reformatting is skipped.',
                ),
            )
        else:
            # reformat configs
            dispatcher = ConfigReformatDispatcher()
            reformatting_summary = {}
            for config in configs:
                config_label = (
                    f'{config.name} ({config.type}, '
                    f'{config.project.name if config.project else "default"}, '
                    f'v{config.version})'
                )
                self.stdout.write(f'{config_label} reformatting:')
                reformatting_status = dispatcher.reformat_config(config)
                reformatting_summary[config_label] = reformatting_status

            # show the reformatting summary
            self.stdout.write('=========================================================')
            self.stdout.write('Configurations reformatting summary status:')
            for config_label, reformatting_status in reformatting_summary.items():
                if reformatting_status == ConfigReformatStatuses.SUCCESS:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'\t{config_label}: successfully reformatted!',
                        ),
                    )
                elif reformatting_status == ConfigReformatStatuses.SKIPPED:
                    self.stdout.write(f'\t{config_label}: reformatting is skipped!')
                elif reformatting_status == ConfigReformatStatuses.FAILED:
                    self.stdout.write(
                        self.style.ERROR(
                            f'\t{config_label}: failed to reformat!',
                        ),
                    )
                else:
                    msg = 'Unknown reformatting status'
                    raise ValueError(msg)
            self.stdout.write('=========================================================')
