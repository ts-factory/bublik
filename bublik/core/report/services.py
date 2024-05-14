# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import json
import os

from bublik import settings
from bublik.data.models import Meta
from bublik.settings import REPORT_CONFIG_COMPONENTS


def get_report_config():
    report_configs_dir = os.path.join(settings.PER_CONF_DIR, 'report_configs')
    for root, _, report_config_files in os.walk(report_configs_dir):
        for report_config_file in report_config_files:
            with open(os.path.join(root, report_config_file)) as rсf:
                try:
                    report_config = json.load(rсf)
                    yield report_config_file, report_config
                except json.decoder.JSONDecodeError:
                    yield report_config_file, None


def get_report_config_by_id(report_config_id):
    for _, report_config in get_report_config():
        if report_config:
            try:
                if str(report_config['id']) == report_config_id:
                    return report_config
            except KeyError:
                continue
    return None


def check_report_config(report_config):
    '''
    Check the passed report config for compliance with the expected format.
    '''
    for key in REPORT_CONFIG_COMPONENTS['required_keys']:
        if key not in report_config.keys():
            msg = f'the required key \'{key}\' is missing in the configuration'
            raise KeyError(msg)

    for test_name, test_configuration in report_config['tests'].items():
        for key in REPORT_CONFIG_COMPONENTS['required_test_keys']:
            if key not in test_configuration.keys():
                msg = (
                    f'the required key \'{key}\' is missing for \'{test_name}\' '
                    'test in the configuration'
                )
                raise KeyError(msg)


def build_report_title(main_pkg, title_content):
    '''
    Form the title of the report according to configuration.
    '''
    meta_labels = Meta.objects.filter(
        metaresult__result__id=main_pkg.id,
        type='label',
        name__in=title_content,
    ).values_list('name', 'value')
    meta_labels = dict(meta_labels)

    title = []
    for title_obj in title_content:
        if title_obj in meta_labels:
            title.append(meta_labels[title_obj])
    return '-'.join(title)
