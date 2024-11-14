# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.core.management.base import BaseCommand
from django.db.models import Q
from rest_framework import serializers

from bublik.data.models import Config, ConfigTypes, GlobalConfigNames
from bublik.data.serializers import ConfigSerializer


def update_config_content(config_instance, new_content):
    serializer = ConfigSerializer(
        instance=config_instance,
        data={'content': new_content},
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    config_instance.content = serializer.validated_data['content']
    config_instance.save()


class Command(BaseCommand):
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
                    update_config_content(report_config, config_data)
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
                                test_config_data['sequences']['percentage_base_value'] = (
                                    test_config_data['percentage_base_value']
                                )
                            if test_config_data['sequence_name_conversion']:
                                test_config_data['sequences']['arg_vals_labels'] = (
                                    test_config_data['sequence_name_conversion']
                                )

                        test_config_data.pop('sequence_group_arg')
                        test_config_data.pop('percentage_base_value')
                        test_config_data.pop('sequence_name_conversion')

                        config_data['tests'][test_name] = test_config_data

                if modified:
                    update_config_content(report_config, config_data)
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

    def update_dashboard_header_structure(self, configs):
        '''
        Reformat passed global per_conf configs content:
        DASHBOARD_HEADER: {<key>: <label>} ->
        DASHBOARD_HEADER: [{"key": <key>, "label": <label>}]
        '''
        per_conf_configs = configs.filter(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigNames.PER_CONF,
        )
        for per_conf_config in per_conf_configs:
            try:
                config_data = per_conf_config.content
                if isinstance(config_data['DASHBOARD_HEADER'], dict):
                    config_data['DASHBOARD_HEADER'] = [
                        {'key': key, 'label': label}
                        for key, label in config_data['DASHBOARD_HEADER'].items()
                    ]
                    update_config_content(per_conf_config, config_data)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{per_conf_config.name} v{per_conf_config.version}: '
                            'the dashboard header structure has been successfully updated!',
                        ),
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{per_conf_config.name} v{per_conf_config.version}: '
                            'the dashboard header structure already updated!',
                        ),
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'{per_conf_config.name} v{per_conf_config.version}: '
                        'failed to update the dashboard header structure: '
                        f'{type(e).__name__}: {e}',
                    ),
                )

    def update_csrf_trusted_origins(self, configs):
        '''
        Reformat passed global per_conf configs content:
        CSRF_TRUSTED_ORIGINS: ["http://origin1", "origin2", "https://origin3"] ->
        CSRF_TRUSTED_ORIGINS: ["http://origin1", "https://origin2", "https://origin3"]
        '''
        per_conf_configs = configs.filter(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigNames.PER_CONF,
        )
        for per_conf_config in per_conf_configs:
            try:
                config_data = per_conf_config.content
                csrf_trusted_origins = config_data.get('CSRF_TRUSTED_ORIGINS', [])
                updated_csrf_trusted_origins = [
                    (
                        origin
                        if origin.startswith(('http://', 'https://'))
                        else f'https://{origin}'
                    )
                    for origin in csrf_trusted_origins
                ]
                if csrf_trusted_origins != updated_csrf_trusted_origins:
                    config_data['CSRF_TRUSTED_ORIGINS'] = updated_csrf_trusted_origins
                    update_config_content(per_conf_config, config_data)
                    self.stdout.write(
                        self.style.WARNING(
                            f'{per_conf_config.name} v{per_conf_config.version}: '
                            'HTTPS has been added to CSRF trusted origins without a scheme. '
                            'Update manually if HTTP is needed.',
                        ),
                    )
                else:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f'{per_conf_config.name} v{per_conf_config.version}: '
                            'CSRF trusted origins already updated!',
                        ),
                    )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(
                        f'{per_conf_config.name} v{per_conf_config.version}: '
                        'failed to update CSRF trusted origins: '
                        f'{type(e).__name__}: {e}',
                    ),
                )

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
        self.update_dashboard_header_structure(configs)
        self.update_csrf_trusted_origins(configs)
