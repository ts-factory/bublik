# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

import logging

from django.core.exceptions import MultipleObjectsReturned

from bublik.core.config.services import ConfigServices
from bublik.data.models import Config, GlobalConfigs
from bublik.data.serializers import ProjectMetaSerializer


logger = logging.getLogger('')


class BaseReformatStep:
    def apply(self, config, **kwargs):
        '''
        Reformats the provided content if it has not been reformatted yet,
        and outputs the execution status. Returns the content.
        '''
        try:
            if not self.applied(config, **kwargs):
                config = self.reformat(config, **kwargs)
                logger.info(f'\tSTEP: {self.__class__.__name__} - OK')
                return config, True
            logger.info(f'\tSTEP: {self.__class__.__name__} - SKIPPED')
            return config, False
        except Exception as err:
            logger.info(f'\tSTEP: {self.__class__.__name__} - FAILED:')
            raise err

    def applied(self, config, **kwargs):
        '''
        Checks whether the step has already been applied.
        '''
        msg = 'Subclasses must implement `applied`.'
        raise NotImplementedError(msg)

    def reformat(self, config, **kwargs):
        '''
        Reformats the provided content.
        '''
        msg = 'Subclasses must implement `reformat`.'
        raise NotImplementedError(msg)


class UpdateAxisXStructure(BaseReformatStep):
    '''
    Reformat passed report config content:
    "axis_x": <str> -> "axis_x": {"arg": <str>}
    '''

    def applied(self, config, **kwargs):
        content = config.content
        for _test_name, test_config_data in content['tests'].items():
            if isinstance(test_config_data['axis_x'], str):
                return False
        return True

    def reformat(self, config, **kwargs):
        content = config.content
        for test_name, test_config_data in content['tests'].items():
            if isinstance(test_config_data['axis_x'], str):
                test_config_data['axis_x'] = {
                    'arg': test_config_data['axis_x'],
                }
                content['tests'][test_name] = test_config_data
        config.content = content
        return config


class UpdateSeqSettingsStructure(BaseReformatStep):
    '''
    Reformat passed report config content:
    Add the 'sequences' foreign key for all sequences settings
    (sequence_group_arg, percentage_base_value, sequence_name_conversion).
    Rename 'sequence_name_conversion' to 'arg_vals_labels'.
    '''

    def applied(self, config, **kwargs):
        content = config.content
        for _test_name, test_config_data in content['tests'].items():
            if 'sequences' not in test_config_data:
                return False
        return True

    def reformat(self, config, **kwargs):
        content = config.content
        for test_name, test_config_data in content['tests'].items():
            if 'sequences' not in test_config_data:
                if test_config_data['sequence_group_arg'] is not None:
                    test_config_data['sequences'] = {
                        'arg': test_config_data['sequence_group_arg'],
                    }
                    if test_config_data['percentage_base_value'] is not None:
                        test_config_data['sequences']['percentage_base_value'] = (
                            test_config_data['percentage_base_value']
                        )
                    if test_config_data['sequence_name_conversion']:
                        test_config_data['sequences']['arg_vals_labels'] = test_config_data[
                            'sequence_name_conversion'
                        ]

                test_config_data.pop('sequence_group_arg')
                test_config_data.pop('percentage_base_value')
                test_config_data.pop('sequence_name_conversion')

                content['tests'][test_name] = test_config_data
        config.content = content
        return config


class UpdateDashboardHeaderStructure(BaseReformatStep):
    '''
    Reformat passed global per_conf config content:
    DASHBOARD_HEADER: {<key>: <label>} ->
    DASHBOARD_HEADER: [{"key": <key>, "label": <label>}]
    '''

    def applied(self, config, **kwargs):
        return not isinstance(config.content['DASHBOARD_HEADER'], dict)

    def reformat(self, config, **kwargs):
        config.content['DASHBOARD_HEADER'] = [
            {'key': key, 'label': label}
            for key, label in config.content['DASHBOARD_HEADER'].items()
        ]
        return config


class UpdateCSRFTrustedOrigins(BaseReformatStep):
    '''
    Reformat passed global per_conf config content:
    CSRF_TRUSTED_ORIGINS: ["http://origin1", "origin2", "https://origin3"] ->
    CSRF_TRUSTED_ORIGINS: ["http://origin1", "https://origin2", "https://origin3"]
    '''

    def applied(self, config, **kwargs):
        content = config.content
        for origin in content.get('CSRF_TRUSTED_ORIGINS', []):
            if not origin.startswith(('http://', 'https://')):
                return False
        return True

    def reformat(self, config, **kwargs):
        config.content['CSRF_TRUSTED_ORIGINS'] = [
            (origin if origin.startswith(('http://', 'https://')) else f'https://{origin}')
            for origin in config.content.get('CSRF_TRUSTED_ORIGINS', [])
        ]
        return config


class RemoveUnsupportedAttributes(BaseReformatStep):
    def applied(self, config, **kwargs):
        schema = ConfigServices.get_schema(
            config.type,
            config.name,
        )
        valid_attributes = schema.get('properties', {}).keys()
        content = config.content
        return not (
            isinstance(content, dict)
            and not schema.get('additionalProperties', True)
            and not set(content.keys()).issubset(set(valid_attributes))
        )

    def reformat(self, config, **kwargs):
        schema = ConfigServices.get_schema(
            config.type,
            config.name,
        )
        # leave only valid attributes
        valid_attributes = schema.get('properties', {}).keys()
        config.content = {k: v for k, v in config.content.items() if k in valid_attributes}
        return config


class UpdateLogsFormat(BaseReformatStep):
    '''
    Reformat passed global references configs content:
    {"LOGS": {"LOGS_BASE": {"uri": ["uri1", "uri2"], "name": "Logs Base"},
    "BUG_KEY": {"uri": ["uri3", "uri4"], "name": "Bugs Base"}},...} ->
    {"LOGS_BASES": [{"uri": ["uri1", "uri2"], "name": "Logs Base"}],
    "ISSUES": {"BUG_KEY": {"uri": ["uri3", "uri4"], "name": "Bugs Base"}}},...}
    '''

    def applied(self, config, **kwargs):
        return 'LOGS' not in config.content

    def reformat(self, config, **kwargs):
        content = config.content
        logs_base = content['LOGS'].pop('LOGS_BASE')
        content['LOGS_BASES'] = [logs_base]
        issues = {
            issue_key: {'name': issue_data['name'], 'uri': issue_data['uri'][0]}
            for issue_key, issue_data in content.pop('LOGS').items()
        }
        if issues:
            content['ISSUES'] = issues
        content['REVISIONS'] = {
            issue_key: {'name': issue_data['name'], 'uri': issue_data['uri'][0]}
            for issue_key, issue_data in content.get('REVISIONS').items()
        }
        config.content = content
        return config


class UpdateProject(BaseReformatStep):
    '''
    Update the project value for all configurations
    according to the PROJECT value in the active main configuration.
    '''

    def applied(self, config, **kwargs):
        if config.project:
            return True
        try:
            per_conf = Config.objects.get_global(GlobalConfigs.PER_CONF.name)
            return not per_conf.content.get('PROJECT')
        except MultipleObjectsReturned:
            return True

    def reformat(self, config, **kwargs):
        per_conf = Config.objects.get_global(GlobalConfigs.PER_CONF.name)
        if not per_conf.content['PROJECT']:
            config.project = None
            return config
        project_meta, _ = ProjectMetaSerializer(
            data={'project_name': per_conf.content['PROJECT']},
        ).get_or_create()
        config.project = project_meta
        return config


class RemoveProjectFromPerConf(BaseReformatStep):
    '''
    Reformat passed global per_conf configs content:
    "PROJECT": "project name" -> None
    '''

    def applied(self, config, **kwargs):
        return 'PROJECT' not in config.content

    def reformat(self, config, **kwargs):
        if not config.is_active:
            config.content.pop('PROJECT', None)
            return config
        # verify whether the PROJECT key from the active main config
        # matches the project field in all other configurations
        project = config.content.get('PROJECT') or None
        config_projects = (
            Config.objects.exclude(id=config.id)
            .values_list('project__value', flat=True)
            .distinct()
        )
        if len(config_projects) != 1 or next(iter(config_projects)) != project:
            msg = (
                'Not all configurations are associated with the project '
                'specified in the main configuration',
            )
            raise Exception(msg)
        # drop PROJECT from content
        config.content.pop('PROJECT')
        return config
