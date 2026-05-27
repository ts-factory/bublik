# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from copy import deepcopy
import json

from pydantic import ValidationError
import pytest

from bublik.mcp.models import JsonLog, TimeStamp
from bublik.mcp.processor import LogProcessor
from bublik.mcp.tests.conftest import EXAMPLES_DIR


NUMERIC_TIMESTAMP = 1705312800.0
FORMATTED_NUMERIC_TIMESTAMP = '10:00:00.000'


def _load_example_data() -> dict:
    with open(EXAMPLES_DIR / 'comprehensive.json') as f:
        return json.load(f)


def _first_log_line(data: dict) -> dict:
    return data['root'][0]['content'][2]['data'][0]


def test_json_log_accepts_legacy_timestamp_object():
    timestamp = {'timestamp': 1711228667.618432, 'formatted': '21:17:47.618'}

    assert TimeStamp.model_validate(timestamp).timestamp == timestamp['timestamp']


def test_json_log_accepts_numeric_timestamp():
    data = _load_example_data()
    line = _first_log_line(data)
    line['timestamp'] = NUMERIC_TIMESTAMP

    log = JsonLog.model_validate(data)
    processor = LogProcessor(log)
    first_line = processor.flat_lines[0]

    assert first_line.timestamp_raw == NUMERIC_TIMESTAMP
    assert first_line.timestamp == FORMATTED_NUMERIC_TIMESTAMP


def test_json_log_rejects_legacy_timestamp_object_without_formatted():
    data = _load_example_data()
    line = _first_log_line(data)
    timestamp = deepcopy(line['timestamp'])
    del timestamp['formatted']
    line['timestamp'] = timestamp

    with pytest.raises(ValidationError):
        JsonLog.model_validate(data)
