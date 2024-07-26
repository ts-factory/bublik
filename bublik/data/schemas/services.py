# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import json
import os


SCHEMAS_DIR = os.path.dirname(__file__)


def load_schema(schema_name):
    with open(os.path.join(SCHEMAS_DIR, f'{schema_name}.json')) as schema_file:
        return json.load(schema_file)
