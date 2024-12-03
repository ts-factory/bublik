# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

import logging

from bublik.core.config.reformatting.steps import (
    BaseReformatStep,
    RemoveUnsupportedAttributes,
    UpdateAxisXStructure,
    UpdateCSRFTrustedOrigins,
    UpdateDashboardHeaderStructure,
    UpdateSeqSettingsStructure,
)


logger = logging.getLogger('')


class ReformatPipeline:
    def __init__(self):
        self.steps = [RemoveUnsupportedAttributes()]

    def add_step(self, step):
        if not isinstance(step, BaseReformatStep):
            msg = 'Step must be an instance of BaseReformatStep'
            raise TypeError(msg)
        # the RemoveUnsupportedAttributes step should be executed last
        self.steps.insert(-1, step)

    def run(self, content, schema):
        reformatted = False
        for step in self.steps:
            content, applied = step.apply(content, schema=schema)
            if applied:
                reformatted = True
        return content, reformatted


class ReportConfigReformatPipeline(ReformatPipeline):
    def __init__(self):
        super().__init__()
        self.add_step(UpdateAxisXStructure())
        self.add_step(UpdateSeqSettingsStructure())


class PerConfConfigReformatPipeline(ReformatPipeline):
    def __init__(self):
        super().__init__()
        self.add_step(UpdateDashboardHeaderStructure())
        self.add_step(UpdateCSRFTrustedOrigins())
