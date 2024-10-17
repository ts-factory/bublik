# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand
from django.db.models import Q
from rest_framework import serializers

from bublik.data.models import Config, ConfigTypes
from bublik.data.serializers import ConfigSerializer


class Command(BaseCommand):
    def update_axis_x_structure(self, configs):
        '''
        Reformat passed report configs content:
        "axis_x": <str> -> "axis_x": {"arg": <str>}
        '''
        report_configs = configs.filter(type=ConfigTypes.REPORT)
        for report_config in report_configs:
            try:
                config_data = report_config.content
                modified = False
                for test_name, test_config_data in config_data['tests'].items():
                    if isinstance(test_config_data['axis_x'], str):
                        modified = True
                        test_config_data['axis_x'] = {
                            'arg': test_config_data['axis_x'],
                        }
                        config_data['tests'][test_name] = test_config_data

                if modified:
                    serializer = ConfigSerializer(
                        instance=report_config,
                        data={'content': config_data},
                        partial=True,
                    )
                    serializer.is_valid(raise_exception=True)
                    report_config.content = serializer.validated_data['content']
                    report_config.save()

                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{report_config.name} v{report_config.version}: '
                            'the x-axis settings structure has been successfully updated!',
                        ),
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{report_config.name} v{report_config.version}: '
                            'the x-axis settings structure already updated!',
                        ),
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'{report_config.name} v{report_config.version}: '
                        f'failed to update x-axis settings structure: {type(e).__name__}: {e}',
                    ),
                )

    def update_seq_settings_structure(self, configs):
        '''
        Reformat passed report configs content:
        Add the 'sequences' foreign key for all sequences settings
        (sequence_group_arg, percentage_base_value, sequence_name_conversion).
        Rename 'sequence_name_conversion' to 'arg_vals_labels'.
        '''
        report_configs = configs.filter(type=ConfigTypes.REPORT)
        for report_config in report_configs:
            try:
                config_data = report_config.content
                modified = False
                for test_name, test_config_data in config_data['tests'].items():
                    if 'sequences' not in test_config_data:
                        modified = True
                        if test_config_data['sequence_group_arg'] is not None:
                            test_config_data['sequences'] = {
                                'arg': test_config_data['sequence_group_arg'],
                            }
                            if test_config_data['percentage_base_value'] is not None:
                                test_config_data['sequences'][
                                    'percentage_base_value'
                                ] = test_config_data['percentage_base_value']
                            if test_config_data['sequence_name_conversion']:
                                test_config_data['sequences'][
                                    'arg_vals_labels'
                                ] = test_config_data['sequence_name_conversion']

                        test_config_data.pop('sequence_group_arg')
                        test_config_data.pop('percentage_base_value')
                        test_config_data.pop('sequence_name_conversion')

                        config_data['tests'][test_name] = test_config_data

                if modified:
                    serializer = ConfigSerializer(
                        instance=report_config,
                        data={'content': config_data},
                        partial=True,
                    )
                    serializer.is_valid(raise_exception=True)
                    report_config.content = serializer.validated_data['content']
                    report_config.save()

                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{report_config.name} v{report_config.version}: '
                            'the sequences settings structure has been successfully updated!',
                        ),
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{report_config.name} v{report_config.version}: '
                            'the sequences settings structure already updated!',
                        ),
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'{report_config.name} v{report_config.version}: '
                        'failed to update sequences settings structure: '
                        f'{type(e).__name__}: {e}',
                    ),
                )

    def get_configs(self, options):
        '''
        Get a list of configurations filtered by the passed types and names.
        '''
        configs = Config.objects.all()
        if not options['type'] and not options['name']:
            return configs

        if not options['name']:
            # we only have valid types as a result of parsing arguments
            return configs.filter(type__in=options['type'])

        if not options['type']:
            # name validation is possible only if there is a type
            return configs.filter(name__in=options['name'])

        types_names = {t: options['name'] for t in options['type']}
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
        configs = self.get_configs(options)
        if not configs:
            self.stdout.write(
                self.style.WARNING(
                    'No valid configurations were found. Reformatting is skipped.',
                ),
            )

        self.update_axis_x_structure(configs)
        self.update_seq_settings_structure(configs)
