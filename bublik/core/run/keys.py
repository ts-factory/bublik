# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import re

from bublik.core.config.services import ConfigServices
from bublik.core.logging import get_task_or_server_logger
from bublik.data.models import GlobalConfigs


logger = get_task_or_server_logger()


def prepare_expected_key(key_str, project_id):
    for ref in re.findall(r'ref://[^, ]+', key_str):
        ref_type = re.search(r'ref://(.*)/', ref).group(1)
        if ref_type not in ConfigServices.getattr_from_global(
            GlobalConfigs.REFERENCES.name,
            'ISSUES',
            project_id,
        ):
            logger.warning(f"{key_str}: '{ref_type}' doesn`t match the project references")

    yield {'meta': {'name': key_str, 'type': 'key'}}
