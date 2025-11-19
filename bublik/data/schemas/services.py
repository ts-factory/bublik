# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import json
import os


SCHEMAS_DIR = os.path.dirname(__file__)


def load_schema(schema_name):
    with open(os.path.join(SCHEMAS_DIR, f'{schema_name}.json')) as schema_file:
        return json.load(schema_file)


def generate_content(schema):
    if 'default' in schema:
        return schema['default']

    schema_type = schema.get('type')

    if schema_type == 'object':
        obj = {}
        for key, prop in schema.get('properties', {}).items():
            value = generate_content(prop)
            if value not in [None, {}]:
                obj[key] = value
        return obj

    if schema_type == 'array':
        return (
            [generate_content(schema['items'])]
            if 'default' in schema.get('items', {})
            else None
        )

    return None
