# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from enum import Enum
import logging
import traceback

from bublik.core.config.reformatting.piplines import (
    MetaConfigReformatPipeline,
    PerConfConfigReformatPipeline,
    ReferencesConfigReformatPipeline,
    ReportConfigReformatPipeline,
)
from bublik.data.models import ConfigTypes, GlobalConfigs
from bublik.data.serializers import ConfigSerializer


logger = logging.getLogger('')


def get_config_data_type(config):
    if config.type == ConfigTypes.REPORT:
        return config.type
    if config.type == ConfigTypes.GLOBAL:
        return config.name
    msg = f'Unknown config data type for config: {config}'
    raise ValueError(msg)


def update_config(reformatted_config):
    serializer = ConfigSerializer(
        instance=reformatted_config,
        data={'content': reformatted_config.content},
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    reformatted_config.save()


class ConfigReformatStatuses(str, Enum):
    '''
    All available config reformatting statuses.
    '''

    SUCCESS = 'success'
    SKIPPED = 'skipped'
    FAILED = 'failed'

    def __str__(self):
        return self.value


class ConfigReformatDispatcher:
    def __init__(self):
        self.pipelines = {
            ConfigTypes.REPORT: ReportConfigReformatPipeline(),
            GlobalConfigs.PER_CONF.name: PerConfConfigReformatPipeline(),
            GlobalConfigs.REFERENCES.name: ReferencesConfigReformatPipeline(),
            GlobalConfigs.META.name: MetaConfigReformatPipeline(),
            GlobalConfigs.TAGS.name: MetaConfigReformatPipeline(),
        }

    def reformat_config(self, config):
        config_data_type = get_config_data_type(config)
        if config_data_type not in self.pipelines:
            logger.warning(f'No pipeline defined for config data type: {config_data_type}')
            return ConfigReformatStatuses.SKIPPED

        pipeline = self.pipelines[config_data_type]

        try:
            reformatted_config, reformatted = pipeline.run(config)
            if reformatted:
                update_config(reformatted_config)
                return ConfigReformatStatuses.SUCCESS
            return ConfigReformatStatuses.SKIPPED
        except Exception:
            tb = traceback.format_exc()
            indented_tb = '\t' + '\t'.join(tb.splitlines(True))
            logger.error(indented_tb)
            return ConfigReformatStatuses.FAILED
