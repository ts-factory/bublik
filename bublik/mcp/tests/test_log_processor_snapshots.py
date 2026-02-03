# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

'''
Snapshot tests for LogProcessor markdown output.

This module uses syrupy for snapshot testing to verify that LogProcessor
produces consistent markdown output across code changes.

Run tests:
    pytest bublik/mcp/tests/test_log_processor_snapshots.py -v

Update snapshots after intentional changes:
    pytest bublik/mcp/tests/test_log_processor_snapshots.py --snapshot-update
'''

from __future__ import annotations

from typing import TYPE_CHECKING

from bublik.mcp.tests.conftest import normalize_dynamic_content


if TYPE_CHECKING:
    from syrupy import SnapshotAssertion

    from bublik.mcp.processor import LogProcessor


class TestOverviewSnapshots:
    '''
    Snapshot tests for get_overview method.
    '''

    def test_overview_markdown(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test overview markdown output without scenario lines.
        '''
        overview = single_processor.get_overview(include_scenario=False)
        md_content = overview.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_overview_with_scenario(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test overview markdown output with scenario lines included.
        '''
        overview = single_processor.get_overview(include_scenario=True)
        md_content = overview.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md


class TestLinesSnapshots:
    '''
    Snapshot tests for line extraction methods.
    '''

    def test_all_lines(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test all lines markdown output.
        '''
        result = single_processor.get_lines(max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_scenario_lines(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test scenario lines markdown output.
        '''
        result = single_processor.get_scenario_lines(max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_line_range(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test line range extraction markdown output.
        '''
        result = single_processor.get_lines(start_line=1, end_line=5, max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md


class TestFilteredLinesSnapshots:
    '''
    Snapshot tests for filtered line extraction.
    '''

    def test_filter_by_error_level(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test filtering by ERROR level.
        '''
        result = single_processor.get_lines(levels=['ERROR'], max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_filter_by_warn_level(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test filtering by WARN level.
        '''
        result = single_processor.get_lines(levels=['WARN'], max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_filter_by_info_level(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test filtering by INFO level.
        '''
        result = single_processor.get_lines(levels=['INFO'], max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_filter_by_multiple_levels(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test filtering by multiple levels (ERROR and WARN).
        '''
        result = single_processor.get_lines(levels=['ERROR', 'WARN'], max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md


class TestLogLineSnapshots:
    '''
    Snapshot tests for individual LogLine formatting.
    '''

    def test_lines_table_format(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test table row format for first 5 lines.
        '''
        lines = single_processor.flat_lines[:5]
        header = '| Line | Depth | Level | Entity:User | Time | Content |'
        separator = '|------|-------|-------|-------------|------|---------|'
        rows = [line.to_markdown(max_content_length=100) for line in lines]
        md_content = '\n'.join([header, separator, *rows])
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md


class TestMultipleTablesSnapshots:
    '''
    Snapshot tests for multiple te-log-table blocks.
    '''

    def test_all_lines_separate_tables(
        self,
        single_processor: LogProcessor,
        snapshot_md: SnapshotAssertion,
    ):
        '''
        Test that all lines render as separate tables.
        '''
        result = single_processor.get_lines(max_content_length=200)
        md_content = result.to_markdown()
        normalized = normalize_dynamic_content(md_content)
        assert normalized == snapshot_md

    def test_line_has_table_index(
        self,
        single_processor: LogProcessor,
    ):
        '''
        Test that LogLine objects have correct table_index values.
        '''
        lines = single_processor.flat_lines
        expected_lines: int = 12

        # comprehensive.json has 2 tables
        # First table: lines 1-8 (table_index=0)
        # Second table: lines 9-12 (table_index=1)
        assert len(lines) == expected_lines

        # Check first table
        for line in lines[:8]:
            assert line.table_index == 0, f'Line {line.line_number} should be in table 0'

        # Check second table
        for line in lines[8:]:
            assert line.table_index == 1, f'Line {line.line_number} should be in table 1'
