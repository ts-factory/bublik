# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from bublik.core.logging import get_task_or_server_logger
from bublik.core.references import REF_PATTERN, resolve_ref


def prepare_expected_key(key_str, project_id):
    logger = get_task_or_server_logger()
    for match in REF_PATTERN.finditer(key_str):
        resolved = resolve_ref(match.group(0), project_id)
        if resolved and resolved[2] is None:
            logger.warning(f"{key_str}: '{resolved[0]}' doesn`t match the project references")

    yield {'meta': {'name': key_str, 'type': 'key'}}
