# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import TYPE_CHECKING

import pytest
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

from bublik.mcp.models import JsonLog
from bublik.mcp.processor import LogProcessor


if TYPE_CHECKING:
    from syrupy import SnapshotAssertion


TESTS_DIR = Path(__file__).parent
EXAMPLES_DIR = TESTS_DIR / 'log_examples'


def normalize_dynamic_content(content: str) -> str:
    '''Normalize dynamic content for stable snapshots.

    This function normalizes timestamps and other dynamic values
    to ensure snapshot comparisons are stable across runs.

    Args:
        content: Markdown content to normalize

    Returns:
        Normalized content with stable placeholders
    '''
    # Normalize ISO timestamps: 2025-01-15T10:00:00.000Z -> <TIMESTAMP>
    content = re.sub(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z',
        '<TIMESTAMP>',
        content,
    )

    # Normalize formatted timestamps: 10:00:00.000 -> <TIME>
    content = re.sub(r'\d{2}:\d{2}:\d{2}\.\d{3}', '<TIME>', content)

    # Normalize Unix timestamps: 1705312800.0 -> <UNIX_TS>
    content = re.sub(r'\b\d{10}\.\d+\b', '<UNIX_TS>', content)

    # Normalize memory addresses: 0x7fff1234 -> <ADDR>
    return re.sub(r'0x[0-9a-fA-F]+', '<ADDR>', content)


class MarkdownSnapshotExtension(SingleFileSnapshotExtension):
    '''Syrupy extension for markdown snapshot files.'''

    _file_extension = 'md'
    _write_mode = WriteMode.TEXT

    def serialize(self, data: str, **_kwargs) -> str:
        '''Serialize data to markdown format.

        Args:
            data: String data to serialize

        Returns:
            Serialized markdown string
        '''
        return str(data)


@pytest.fixture
def snapshot_md(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    '''Fixture providing markdown snapshot assertion.

    Args:
        snapshot: Base syrupy snapshot fixture

    Returns:
        Snapshot assertion configured for markdown files
    '''
    return snapshot.use_extension(MarkdownSnapshotExtension)


@pytest.fixture
def single_example_log() -> JsonLog:
    '''Load comprehensive.json for tests.

    Returns:
        Validated JsonLog instance
    '''
    example_file = EXAMPLES_DIR / 'comprehensive.json'
    if not example_file.exists():
        pytest.skip(f'Example file not found: {example_file}')

    with open(example_file) as f:
        data = json.load(f)
    return JsonLog.model_validate(data)


@pytest.fixture
def single_processor(single_example_log: JsonLog) -> LogProcessor:
    '''Create LogProcessor from comprehensive.json.

    Args:
        single_example_log: Validated JsonLog instance

    Returns:
        LogProcessor instance
    '''
    return LogProcessor(single_example_log)
