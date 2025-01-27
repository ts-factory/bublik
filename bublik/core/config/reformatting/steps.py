# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

import logging


logger = logging.getLogger('')


class BaseReformatStep:
    def apply(self, content, **kwargs):
        '''
        Reformats the provided content if it has not been reformatted yet,
        and outputs the execution status. Returns the content.
        '''
        try:
            if not self.applied(content, **kwargs):
                content = self.reformat(content, **kwargs)
                logger.info(f'\tSTEP: {self.__class__.__name__} - OK')
                return content, True
            logger.info(f'\tSTEP: {self.__class__.__name__} - SKIPPED')
            return content, False
        except Exception as err:
            logger.info(f'\tSTEP: {self.__class__.__name__} - FAILED:')
            raise err

    def applied(self, content, **kwargs):
        '''
        Checks whether the step has already been applied.
        '''
        msg = 'Subclasses must implement `applied`.'
        raise NotImplementedError(msg)

    def reformat(self, content, **kwargs):
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

    def applied(self, content, **kwargs):
        for _test_name, test_config_data in content['tests'].items():
            if isinstance(test_config_data['axis_x'], str):
                return False
        return True

    def reformat(self, content, **kwargs):
        for test_name, test_config_data in content['tests'].items():
            if isinstance(test_config_data['axis_x'], str):
                test_config_data['axis_x'] = {
                    'arg': test_config_data['axis_x'],
                }
                content['tests'][test_name] = test_config_data
        return content


class UpdateSeqSettingsStructure(BaseReformatStep):
    '''
    Reformat passed report config content:
    Add the 'sequences' foreign key for all sequences settings
    (sequence_group_arg, percentage_base_value, sequence_name_conversion).
    Rename 'sequence_name_conversion' to 'arg_vals_labels'.
    '''

    def applied(self, content, **kwargs):
        for _test_name, test_config_data in content['tests'].items():
            if 'sequences' not in test_config_data:
                return False
        return True

    def reformat(self, content, **kwargs):
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
        return content


class UpdateDashboardHeaderStructure(BaseReformatStep):
    '''
    Reformat passed global per_conf config content:
    DASHBOARD_HEADER: {<key>: <label>} ->
    DASHBOARD_HEADER: [{"key": <key>, "label": <label>}]
    '''

    def applied(self, content, **kwargs):
        return not isinstance(content['DASHBOARD_HEADER'], dict)

    def reformat(self, content, **kwargs):
        content['DASHBOARD_HEADER'] = [
            {'key': key, 'label': label} for key, label in content['DASHBOARD_HEADER'].items()
        ]
        return content


class UpdateCSRFTrustedOrigins(BaseReformatStep):
    '''
    Reformat passed global per_conf config content:
    CSRF_TRUSTED_ORIGINS: ["http://origin1", "origin2", "https://origin3"] ->
    CSRF_TRUSTED_ORIGINS: ["http://origin1", "https://origin2", "https://origin3"]
    '''

    def applied(self, content, **kwargs):
        for origin in content.get('CSRF_TRUSTED_ORIGINS', []):
            if not origin.startswith(('http://', 'https://')):
                return False
        return True

    def reformat(self, content, **kwargs):
        content['CSRF_TRUSTED_ORIGINS'] = [
            (origin if origin.startswith(('http://', 'https://')) else f'https://{origin}')
            for origin in content.get('CSRF_TRUSTED_ORIGINS', [])
        ]
        return content


class RemoveUnsupportedAttributes(BaseReformatStep):
    def applied(self, content, **kwargs):
        schema = kwargs.get('schema')
        valid_attributes = schema.get('properties', {}).keys()
        return not (
            isinstance(content, dict)
            and not schema.get('additionalProperties', True)
            and not set(content.keys()).issubset(set(valid_attributes))
        )

    def reformat(self, content, **kwargs):
        schema = kwargs.get('schema')
        # leave only valid attributes
        valid_attributes = schema.get('properties', {}).keys()
        return {k: v for k, v in content.items() if k in valid_attributes}


class UpdateLogsFormat(BaseReformatStep):
    '''
    Reformat passed global references configs content:
    {"LOGS": {"LOGS_BASE": {"uri": ["uri1", "uri2"], "name": "Logs Base"},
    "BUG_KEY": {"uri": ["uri3", "uri4"], "name": "Bugs Base"}},...} ->
    {"LOGS_BASES": [{"uri": ["uri1", "uri2"], "name": "Logs Base"}],
    "ISSUES": {"BUG_KEY": {"uri": ["uri3", "uri4"], "name": "Bugs Base"}}},...}
    '''

    def applied(self, content, **kwargs):
        return 'LOGS' not in content

    def reformat(self, content, **kwargs):
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
        return content
