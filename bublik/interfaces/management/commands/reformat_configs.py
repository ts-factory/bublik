# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand

from bublik.data.models import Config, ConfigTypes
from bublik.data.serializers import ConfigSerializer


class Command(BaseCommand):
    def update_axis_x_structure(self, report_configs):
        '''
        Reformat passed report configs content:
        "axis_x": <str> -> "axis_x": {"arg": <str>}
        '''
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
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'{report_config.name} v{report_config.version}: '
                        f'failed to update x-axis settings structure: {type(e).__name__}: {e}',
                    ),
                )

    def update_seq_settings_structure(self, report_configs):
        '''
        Reformat passed report configs content:
        Add the 'sequences' foreign key for all sequences settings
        (sequence_group_arg, percentage_base_value, sequence_name_conversion).
        Rename 'sequence_name_conversion' to 'arg_vals_labels'.
        '''
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
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'{report_config.name} v{report_config.version}: '
                        'failed to update sequences settings structure: '
                        f'{type(e).__name__}: {e}',
                    ),
                )

    def handle(self, *args, **options):
        configs = Config.objects.all()
        self.update_axis_x_structure(configs.filter(type=ConfigTypes.REPORT))
        self.update_seq_settings_structure(configs.filter(type=ConfigTypes.REPORT))
