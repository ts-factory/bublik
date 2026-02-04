# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

'''
Log processor for working with validated JsonLog data.
'''

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, ClassVar

from bublik.mcp.models import (
    JsonLog,
    LogContent,
    LogHeaderInfo,
    LogLine,
    LogLinesResult,
    LogOverview,
)


if TYPE_CHECKING:
    from bublik.mcp.models import LogTableData, SnifferContentItem


class LogProcessor:
    '''
    Process validated JsonLog with extraction and filtering capabilities.

    This class takes a validated JsonLog Pydantic model and provides methods
    to extract overview information, filter log lines, and convert to markdown.

    Example:
        >>> from bublik.mcp.models import JsonLog
        >>> log = JsonLog.model_validate(log_data)
        >>> processor = LogProcessor(log)
        >>> overview = processor.get_overview(include_scenario=True)
        >>> print(overview.to_markdown())
    '''

    SCENARIO_USERS: ClassVar[list[str]] = ['Step', 'Artifact', 'Self', 'Verdict']

    def __init__(self, log: JsonLog):
        '''
        Initialize LogProcessor with validated JsonLog.

        Args:
            log: Validated JsonLog Pydantic model instance
        '''
        self.log = log
        self._flat_lines: list[LogLine] | None = None
        self._main_entity: str | None = None
        self._entity_user_map: dict[str, set[str]] | None = None

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def flat_lines(self) -> list[LogLine]:
        '''
        Lazily flatten all nested log lines into a flat list.

        Returns:
            List of LogLine instances from all log table blocks
        '''
        if self._flat_lines is None:
            self._flat_lines = self._flatten_all_lines()
        return self._flat_lines

    @property
    def main_test_entity(self) -> str | None:
        '''
        Identify the main test entity from log lines.

        The main entity is determined by (in priority order):
        1. First entity that has a user named "TAPI Jumps"
        2. First entity name containing 'test' (case-insensitive)
        3. Fallback to most common entity name

        This matches the logic from the frontend JavaScript implementation.

        Returns:
            Main test entity name or None if no lines exist
        '''
        if self._main_entity is None:
            if not self.flat_lines:
                return None

            # Priority 1: Find first entity with "TAPI Jumps" user
            for line in self.flat_lines:
                if line.user_name == 'TAPI Jumps':
                    self._main_entity = line.entity_name
                    return self._main_entity

            # Priority 2: Prefer entity with 'test' in name
            entities = Counter(line.entity_name for line in self.flat_lines)
            for entity in entities:
                if 'test' in entity.lower():
                    self._main_entity = entity
                    break
            else:
                # Priority 3: Fallback to most common
                self._main_entity = entities.most_common(1)[0][0] if entities else None
        return self._main_entity

    @property
    def all_entity_user_pairs(self) -> list[str]:
        '''
        Get all unique entity:user combinations from log lines.

        Returns:
            Sorted list of "entity_name:user_name" strings
        '''
        pairs = {f'{line.entity_name}:{line.user_name}' for line in self.flat_lines}
        return sorted(pairs)

    @property
    def scenario_filters(self) -> list[str]:
        '''
        Get entity:user pairs for scenario filter.

        Scenario filter includes:
        - {main_entity}:Step
        - {main_entity}:Artifact
        - {main_entity}:Self
        - {main_entity}:Verdict
        - Tester:Run (if exists)

        Returns:
            List of "entity_name:user_name" strings for scenario filtering
        '''
        main_entity = self.main_test_entity
        if not main_entity:
            return []

        all_pairs = set(self.all_entity_user_pairs)
        filters = []

        # Add main entity users
        for user in self.SCENARIO_USERS:
            pair = f'{main_entity}:{user}'
            if pair in all_pairs:
                filters.append(pair)

        # Add Tester:Run if exists
        if 'Tester:Run' in all_pairs:
            filters.append('Tester:Run')

        return filters

    # =========================================================================
    # Overview Methods
    # =========================================================================

    def get_overview(
        self,
        include_scenario: bool = True,
        max_content_length: int | None = None,
    ) -> LogOverview:
        '''
        Extract full log overview with metadata from all te-log-meta blocks.

        Args:
            include_scenario: Include scenario-related log lines in overview
            max_content_length: Maximum content length for scenario lines,
                applies truncation if set

        Returns:
            LogOverview instance with all metadata and optional scenario lines

        Raises:
            ValueError: If no header block is found in the log
        '''
        # Collect ALL header blocks and pagination
        headers: list = []
        pagination = None

        for page_block in self.log.root:
            if page_block.pagination:
                pagination = page_block.pagination
            for content_block in page_block.content:
                if content_block.type == 'te-log-meta':
                    headers.append(content_block)

        if not headers:
            msg = 'No header block found in log'
            raise ValueError(msg)

        # Use first header as primary (for backward compatibility)
        primary_header = headers[0]

        # Build additional headers list

        additional_headers = []
        for header in headers[1:]:
            authors = []
            if header.meta.authors:
                authors = [a.email for a in header.meta.authors]

            additional_headers.append(
                LogHeaderInfo(
                    test_id=header.entity_model.id,
                    test_name=header.entity_model.name,
                    entity_type=header.entity_model.entity,
                    result=header.entity_model.result,
                    error=header.entity_model.error,
                    extended_properties=header.entity_model.extended_properties,
                    start=header.meta.start,
                    end=header.meta.end,
                    duration=header.meta.duration,
                    parameters=(list(header.meta.parameters) if header.meta.parameters else []),
                    verdicts=list(header.meta.verdicts) if header.meta.verdicts else [],
                    artifacts=list(header.meta.artifacts) if header.meta.artifacts else [],
                    requirements=(
                        list(header.meta.requirements) if header.meta.requirements else []
                    ),
                    authors=authors,
                    objective=header.meta.objective,
                    description_url=(
                        str(header.meta.description.url) if header.meta.description else None
                    ),
                    description_text=(
                        header.meta.description.text if header.meta.description else None
                    ),
                ),
            )

        level_counts = Counter(line.level for line in self.flat_lines)

        authors = []
        if primary_header.meta.authors:
            authors = [a.email for a in primary_header.meta.authors]

        return LogOverview(
            test_id=primary_header.entity_model.id,
            test_name=primary_header.entity_model.name,
            entity_type=primary_header.entity_model.entity,
            result=primary_header.entity_model.result,
            error=primary_header.entity_model.error,
            extended_properties=primary_header.entity_model.extended_properties,
            start=primary_header.meta.start,
            end=primary_header.meta.end,
            duration=primary_header.meta.duration,
            cur_page=pagination.cur_page if pagination else None,
            pages_count=pagination.pages_count if pagination else None,
            parameters=(
                list(primary_header.meta.parameters) if primary_header.meta.parameters else []
            ),
            verdicts=(
                list(primary_header.meta.verdicts) if primary_header.meta.verdicts else []
            ),
            artifacts=(
                list(primary_header.meta.artifacts) if primary_header.meta.artifacts else []
            ),
            requirements=(
                list(primary_header.meta.requirements)
                if primary_header.meta.requirements
                else []
            ),
            authors=authors,
            objective=primary_header.meta.objective,
            description_url=(
                str(primary_header.meta.description.url)
                if primary_header.meta.description
                else None
            ),
            description_text=(
                primary_header.meta.description.text
                if primary_header.meta.description
                else None
            ),
            additional_headers=additional_headers,
            total_lines=len(self.flat_lines),
            level_counts=dict(level_counts),
            entity_user_pairs=self.all_entity_user_pairs,
            scenario_lines=(
                self.get_scenario_lines(max_content_length=max_content_length).lines
                if include_scenario
                else []
            ),
        )

    # =========================================================================
    # Line Extraction Methods
    # =========================================================================

    def get_lines(
        self,
        start_line: int | None = None,
        end_line: int | None = None,
        levels: list[str] | None = None,
        entity_names: list[str] | None = None,
        user_names: list[str] | None = None,
        entity_user_pairs: list[str] | None = None,
        table_index: int = 0,
        max_content_length: int | None = 1200,
    ) -> LogLinesResult:
        '''
        Extract and filter lines from log.

        Supports both range-based filtering (by line numbers) and content-based
        filtering (by levels, entities, users). All filters are AND-combined.

        Args:
            start_line: Starting line number (inclusive), None for start
            end_line: Ending line number (inclusive), None for end
            levels: Filter by log levels (ERROR, WARN, INFO, VERB, PACKET, RING)
            entity_names: Filter by entity names
            user_names: Filter by user names
            entity_user_pairs: Filter by "entity:user" combinations
            table_index: Index of log table block (currently unused, reserved)
            max_content_length: Maximum content length, applies truncation if set

        Returns:
            LogLinesResult with filtered and optionally truncated lines
        '''
        lines = self.flat_lines

        # Apply all filters (range and content)
        if start_line is not None:
            lines = [line for line in lines if line.line_number >= start_line]
        if end_line is not None:
            lines = [line for line in lines if line.line_number <= end_line]
        if levels:
            lines = [line for line in lines if line.level in levels]
        if entity_names:
            lines = [line for line in lines if line.entity_name in entity_names]
        if user_names:
            lines = [line for line in lines if line.user_name in user_names]
        if entity_user_pairs:
            lines = [
                line
                for line in lines
                if f'{line.entity_name}:{line.user_name}' in entity_user_pairs
            ]

        # Add parent context for all matched lines
        lines = self._add_parent_context(lines)

        # Apply truncation if specified
        if max_content_length is not None:
            lines = [line.truncate_content(max_content_length) for line in lines]

        # Build comprehensive filter description
        filter_parts = []
        if start_line is not None or end_line is not None:
            filter_parts.append(f"lines {start_line or 'start'}-{end_line or 'end'}")
        if levels:
            filter_parts.append(f'levels={levels}')
        if entity_names:
            filter_parts.append(f'entities={entity_names}')
        if user_names:
            filter_parts.append(f'users={user_names}')
        if entity_user_pairs:
            filter_parts.append(f'pairs={entity_user_pairs}')
        if max_content_length:
            filter_parts.append(f'truncated to {max_content_length} chars')

        return LogLinesResult(
            lines=lines,
            total_count=len(lines),
            filter_applied=', '.join(filter_parts) if filter_parts else None,
        )

    def _add_parent_context(self, lines: list[LogLine]) -> list[LogLine]:
        '''
        Add parent lines for all lines in the list.

        For each line with a parent, find and include the parent line.
        Recursively include all ancestors to maintain full context.

        Args:
            lines: Filtered lines that may have parents

        Returns:
            List with original lines plus all parent lines, sorted by line_number
        '''
        # Create a lookup map from all flat lines for parent reference
        line_map = {line.line_number: line for line in self.flat_lines}

        # Collect all ancestors (parents) for filtered lines
        parent_line_numbers = set()
        for line in lines:
            current = line
            while current.parent_line_number is not None:
                parent_line_number = current.parent_line_number
                if parent_line_number in parent_line_numbers:
                    break
                parent_line_numbers.add(parent_line_number)
                current = line_map.get(parent_line_number)
                if current is None:
                    break

        # Build result with matched lines + parents
        result = list(lines)
        for parent_num in parent_line_numbers:
            parent_line = line_map.get(parent_num)
            if parent_line and parent_line not in result:
                result.append(parent_line)

        # Sort by line_number to maintain proper order
        result.sort(key=lambda x: x.line_number)

        return result

    def get_scenario_lines(self, max_content_length: int | None = 200) -> LogLinesResult:
        '''
        Get scenario-related log lines.

        Scenario lines include: Step, Artifact, Self, Verdict from main entity,
        plus Tester:Run if present.

        Args:
            max_content_length: Maximum content length, applies truncation if set

        Returns:
            LogLinesResult containing scenario lines
        '''
        filters = self.scenario_filters
        if not filters:
            return LogLinesResult(
                lines=[],
                total_count=0,
                filter_applied='scenario (no filters matched)',
            )

        lines = [
            line
            for line in self.flat_lines
            if f'{line.entity_name}:{line.user_name}' in filters
        ]

        lines = self._add_parent_context(lines)

        if max_content_length is not None:
            lines = [line.truncate_content(max_content_length) for line in lines]

        return LogLinesResult(
            lines=lines,
            total_count=len(lines),
            filter_applied='scenario',
        )

    def get_test_lines(self) -> LogLinesResult:
        '''
        Get all lines from the main test entity.

        Returns:
            LogLinesResult containing all lines from main test entity
        '''
        main_entity = self.main_test_entity
        if not main_entity:
            return LogLinesResult(
                lines=[],
                total_count=0,
                filter_applied='test entity (not found)',
            )

        lines = [line for line in self.flat_lines if line.entity_name == main_entity]
        lines = self._add_parent_context(lines)

        return LogLinesResult(
            lines=lines,
            total_count=len(lines),
            filter_applied=f'test entity: {main_entity}',
        )

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _flatten_all_lines(self) -> list[LogLine]:
        '''
        Flatten all log table data into LogLine instances.

        Returns:
            List of LogLine instances from all log table blocks,
            each with table_index indicating source table.
        '''
        lines = []
        table_index = 0
        for page_block in self.log.root:
            for content_block in page_block.content:
                if content_block.type == 'te-log-table':
                    lines.extend(
                        self._flatten_table_data(
                            content_block.data,
                            table_index=table_index,
                        ),
                    )
                    table_index += 1
        return lines

    def _flatten_table_data(
        self,
        data: list[LogTableData],
        depth: int = 0,
        parent_line_number: int | None = None,
        table_index: int = 0,
    ) -> list[LogLine]:
        '''
        Recursively flatten log table data with depth and table tracking.

        Args:
            data: List of LogTableData instances
            depth: Current nesting depth
            parent_line_number: Line number of parent item (None for root level)
            table_index: Index of the source te-log-table block

        Returns:
            Flattened list of LogLine instances
        '''
        lines = []
        for item in data:
            # Extract level as string
            level = item.level.root
            if hasattr(level, 'value'):
                level = level.value

            line = LogLine(
                line_number=item.line_number,
                level=str(level),
                entity_name=item.entity_name,
                user_name=item.user_name,
                timestamp=item.timestamp.formatted,
                timestamp_raw=item.timestamp.timestamp,
                content=self._extract_content(item.log_content),
                content_type=self._get_content_type(item.log_content),
                depth=depth,
                parent_line_number=parent_line_number,
                table_index=table_index,
            )
            lines.append(line)

            if item.children:
                lines.extend(
                    self._flatten_table_data(
                        item.children,
                        depth + 1,
                        parent_line_number=item.line_number,
                        table_index=table_index,
                    ),
                )

        return lines

    def _escape_markdown_table_cell(self, text: str) -> str:
        '''
        Escape pipe characters for markdown tables.

        Args:
            text: Text to escape

        Returns:
            Escaped text with | replaced by \\|
        '''
        return str(text).replace('|', '\\|')

    def _format_file_block(self, content: str) -> str:
        '''
        Format file content in a code block.

        Args:
            content: File content string

        Returns:
            Markdown code block with escaped backticks
        '''
        escaped = content.replace('```', '\\`\\`\\`')
        return f'```\n{escaped}\n```'

    def _format_memory_dump_table(self, dump: list[list[str]]) -> str:
        '''
        Format memory dump as a markdown table.

        Args:
            dump: List of lists of strings representing rows

        Returns:
            Markdown table with memory dump data
        '''
        if not dump:
            return '| (empty memory dump) |'

        num_cols = max(len(row) for row in dump)
        header = '| ' + ' | '.join(f'Col{i}' for i in range(num_cols)) + ' |'
        separator = '|' + '|'.join(['---'] * num_cols) + '|'

        lines = [header, separator]
        for row in dump:
            escaped_cells = [self._escape_markdown_table_cell(cell) for cell in row]
            padded_cells = escaped_cells + [''] * (num_cols - len(escaped_cells))
            lines.append('| ' + ' | '.join(padded_cells) + ' |')

        return '\n'.join(lines)

    def _format_sniffer_table(self, content: list[SnifferContentItem]) -> str:
        '''
        Format packet sniffer content as an expanded table.

        Args:
            content: List of SnifferContentItem instances

        Returns:
            Markdown table with expanded sniffer items
        '''
        if not content:
            return '| (empty packet sniffer) |'

        max_content_cols = max(len(item.content) for item in content)
        headers = ['Label'] + [f'Content{i + 1}' for i in range(max_content_cols)]
        header_row = '| ' + ' | '.join(headers) + ' |'
        separator = '|' + '|'.join(['---'] * len(headers)) + '|'

        lines = [header_row, separator]
        for item in content:
            cells = [self._escape_markdown_table_cell(item.label)]
            padded_content = item.content + [''] * (max_content_cols - len(item.content))
            cells.extend(self._escape_markdown_table_cell(c) for c in padded_content)
            lines.append('| ' + ' | '.join(cells) + ' |')

        return '\n'.join(lines)

    def _extract_content(self, log_content: list[LogContent]) -> str:
        '''
        Extract text content from log_content blocks.

        Args:
            log_content: List of content blocks

        Returns:
            Combined text content string with markdown tables for complex types
        '''
        texts = []
        for block in log_content:
            if block.type == 'te-log-table-content-text':
                texts.append(block.content)
            elif block.type == 'te-log-table-content-file':
                texts.append(self._format_file_block(block.content))
            elif block.type == 'te-log-table-content-mi':
                content = block.content
                if hasattr(content, 'type') and content.type == 'measurement':
                    name = getattr(content, 'name', None) or 'unnamed'
                    texts.append(f'[MEASUREMENT: {name}]')
                else:
                    texts.append('[MEASUREMENT]')
            elif block.type == 'te-log-table-content-memory-dump':
                texts.append(self._format_memory_dump_table(block.dump))
            elif block.type == 'te-log-table-content-packet-sniffer':
                texts.append(self._format_sniffer_table(block.content))
        return '\n\n'.join(texts)

    def _get_content_type(self, log_content: list[LogContent]) -> str:
        '''
        Determine the primary content type from log_content blocks.

        Args:
            log_content: List of content blocks

        Returns:
            Content type string: "text", "file", "measurement", "memory_dump",
            "packet", or "mixed"
        '''
        if not log_content:
            return 'empty'

        types = set()
        for block in log_content:
            if block.type == 'te-log-table-content-text':
                types.add('text')
            elif block.type == 'te-log-table-content-file':
                types.add('file')
            elif block.type == 'te-log-table-content-mi':
                types.add('measurement')
            elif block.type == 'te-log-table-content-memory-dump':
                types.add('memory_dump')
            elif block.type == 'te-log-table-content-packet-sniffer':
                types.add('packet')

        if len(types) == 1:
            return types.pop()
        return 'mixed'
