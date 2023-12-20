# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging
import re

from references import References


logger = logging.getLogger('bublik.server')


def prepare_expected_key(key_str):
    for ref in re.findall(r'ref://[^, ]+', key_str):
        ref_type = re.search(r'ref://(.*)/', ref).group(1)
        if ref_type not in References.logs.keys():
            logger.warning(f"{key_str}: '{ref_type}' doesn`t match the project references")

    yield {'meta': {'name': key_str, 'type': 'key'}}
