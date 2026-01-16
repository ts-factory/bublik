# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
from __future__ import annotations

from collections import Counter
from enum import Enum
from itertools import groupby
from typing import Any, Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel


MAX_PAIRS_TO_SHOW = 20


class Version(Enum):
    v1 = 'v1'


class LogPagination(BaseModel):
    model_config = ConfigDict(extra='forbid')

    cur_page: int
    pages_count: int


class LogParameter(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    value: str


class LogAuthor(BaseModel):
    model_config = ConfigDict(extra='forbid')

    email: str


class LogDescription(BaseModel):
    model_config = ConfigDict(extra='forbid')

    url: AnyUrl
    text: str


class LogVerdict(BaseModel):
    model_config = ConfigDict(extra='forbid')

    verdict: str
    level: LogLevelSchema


class LogArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid')

    level: LogLevelSchema
    artifact: str


class LogLevelEnum(Enum):
    ERROR = 'ERROR'
    WARN = 'WARN'
    INFO = 'INFO'
    VERB = 'VERB'
    PACKET = 'PACKET'
    RING = 'RING'


class LogLevelSchema(RootModel[LogLevelEnum | str]):
    root: LogLevelEnum | str


class LogEntityModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    id: str = Field(..., description='Test or package id')
    name: str = Field(..., description='Test or package name')
    entity: str = Field(..., description='Entity type')
    result: str = Field(..., description='Result of the test or package')
    error: str | None = Field(
        None,
        description='If error message is present result will be in red badge',
    )
    extended_properties: dict[str, str | float] = Field(
        ...,
        description='Additional properties to add such as hash/tin',
    )


class LogMeta(BaseModel):
    model_config = ConfigDict(extra='forbid')

    start: str = Field(..., description='date string')
    end: str = Field(..., description='date string')
    duration: str = Field(..., description='duration of the test')
    parameters: list[LogParameter] | None = Field(
        None,
        description='Optional list of parameters',
    )
    verdicts: list[LogVerdict] | None = Field(None, description='Optional list of verdicts')
    objective: str | None = Field(None, description='Optional objective')
    requirements: list[str] | None = Field(None, description='Optional list of requirements')
    authors: list[LogAuthor] | None = Field(None, description='Optional List of authors')
    artifacts: list[LogArtifact] | None = Field(None, description='Optional list of artifacts')
    description: LogDescription | None = Field(
        None,
        description='Optional description with external url',
    )


class LogContentTextBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-table-content-text'] = Field('te-log-table-content-text')
    content: str = Field(..., description='Text content')


class LogContentMemoryDump(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-table-content-memory-dump'] = Field(
        'te-log-table-content-memory-dump',
    )
    dump: list[list[str]] = Field(..., description='Array of arrays of strings')


class LogContentFile(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-table-content-file'] = Field('te-log-table-content-file')
    content: str = Field(..., description='Content string will display as preformatted text')


class MeasurementEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')

    aggr: str
    value: float
    base_units: str
    multiplier: str


class MeasurementResultEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: str
    description: str
    name: str | None = None
    entries: list[MeasurementEntry]


class ChartType(Enum):
    line_graph = 'line-graph'


class AxisX(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: str | None = None
    name: str | None = None


class AxisYItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: str
    name: str | None = None


class MeasurementChartView(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    type: ChartType
    title: str
    axis_x: AxisX
    axis_y: list[AxisYItem] | None = None


class LogContentMiChart(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['measurement'] = Field('measurement')
    version: int
    tool: str
    name: str | None = None
    description: str | None = None
    results: list[MeasurementResultEntry] = Field(..., description='Array of entries')
    views: list[MeasurementChartView] = Field(..., description='Array of views')


class LogContentMi(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-table-content-mi'] = Field('te-log-table-content-mi')
    content: LogContentMiChart | dict[str, Any]


class SnifferContentItem(BaseModel):
    model_config = ConfigDict(extra='forbid')

    label: str
    content: list[str]


class LogContentSnifferPacket(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-table-content-packet-sniffer'] = Field(
        'te-log-table-content-packet-sniffer',
    )
    content: list[SnifferContentItem]


class TimeStamp(BaseModel):
    timestamp: float = Field(..., description='Timestamp in seconds')
    formatted: str = Field(..., description='Formatted timestamp')


LogContent = (
    LogContentTextBlock
    | LogContentMemoryDump
    | LogContentFile
    | LogContentMi
    | LogContentSnifferPacket
)


class LogTableData(BaseModel):
    model_config = ConfigDict(extra='forbid')

    line_number: int
    level: LogLevelSchema
    entity_name: str
    user_name: str
    timestamp: TimeStamp
    log_content: list[LogContent] = Field(
        ...,
        description='Log content accepts series of blocks for displaying data',
    )
    children: list[LogTableData] | None = Field(None, description='Represents nesting level')


LogTableData.model_rebuild()


class LogHeaderBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-meta'] = Field('te-log-meta')
    entity_model: LogEntityModel
    meta: LogMeta = Field(..., description='Meta information')


class LogEntityListBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-entity-list'] = Field('te-log-entity-list')
    items: list[LogEntityModel]


class LogTableBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log-table'] = Field('te-log-table')
    data: list[LogTableData]


class LogPageBlock(BaseModel):
    model_config = ConfigDict(extra='forbid')

    type: Literal['te-log'] = Field('te-log')
    pagination: LogPagination | None = Field(
        None,
        description='Pagination object `cur_page: 0` represents to display all pages',
    )
    content: list[LogHeaderBlock | LogEntityListBlock | LogTableBlock]


class JsonLog(BaseModel):
    model_config = ConfigDict(extra='forbid')

    version: Version = Field(..., description='Version of the API used')
    root: list[LogPageBlock] = Field(..., description='Root entry for all block')


# ============================================================================
# Log Processing Models (for LogProcessor output)
# ============================================================================


class LogLine(BaseModel):
    '''Single log line with markdown conversion support.'''

    line_number: int
    level: str
    entity_name: str
    user_name: str
    timestamp: str  # Formatted timestamp
    timestamp_raw: float  # Unix timestamp
    content: str  # Extracted/simplified text content
    content_type: str  # "text", "file", "measurement", "memory_dump", "packet", "mixed"
    depth: int = 0  # Nesting depth
    parent_line_number: int | None = None  # Parent's line_number if depth > 0
    content_truncated: bool = False  # Indicates if content was truncated
    original_content_length: int | None = None  # Original length before truncation
    table_index: int = 0  # Index of source te-log-table block

    def truncate_content(self, max_length: int) -> LogLine:
        '''Create a copy with truncated content.

        Args:
            max_length: Maximum content length

        Returns:
            New LogLine with truncated content and metadata
        '''
        if len(self.content) <= max_length:
            return self

        return self.model_copy(
            update={
                'content': self.content[:max_length] + '... [truncated]',
                'content_truncated': True,
                'original_content_length': len(self.content),
            },
        )

    def to_markdown(self, max_content_length: int | None = None) -> str:
        '''Convert to markdown table row.

        Args:
            max_content_length: Optional max length for content truncation

        Returns:
            Markdown table row string
        '''
        content = self.content

        if (
            max_content_length
            and not self.content_truncated
            and len(content) > max_content_length
        ):
            content = content[:max_content_length] + '...'

        # If already truncated, show indicator
        if self.content_truncated:
            content = f'[TRUNCATED: {self.original_content_length} chars] {content}'

        # Escape pipe characters in content for markdown tables
        content = content.replace('|', '\\|').replace('\n', ' ')
        return (
            f'| {self.line_number} | {self.depth} | {self.level} | '
            f'{self.entity_name}:{self.user_name} | {self.timestamp} | {content} |'
        )

    def to_markdown_full(self) -> str:
        '''Convert to full markdown block with all details.

        Returns:
            Full markdown representation of the log line
        '''
        lines = [
            f'### Line {self.line_number}',
            f'- **Level:** {self.level}',
            f'- **Entity:** {self.entity_name}',
            f'- **User:** {self.user_name}',
            f'- **Time:** {self.timestamp}',
            f'- **Depth:** {self.depth}',
        ]
        if self.parent_line_number:
            lines.append(f'- **Parent Line:** {self.parent_line_number}')
        lines.extend(
            [
                f'- **Type:** {self.content_type}',
                '',
                '```',
                self.content,
                '```',
            ],
        )
        return '\n'.join(lines)


class LogLinesResult(BaseModel):
    '''Result of line extraction/filtering with markdown support.'''

    lines: list[LogLine]
    total_count: int
    filter_applied: str | None = None  # Description of filter

    def to_markdown(self, max_content_length: int | None = None) -> str:
        '''Convert to markdown table with separate tables per te-log-table block.

        Args:
            max_content_length: Optional max length for content truncation

        Returns:
            Markdown table string with separate tables grouped by table_index
        '''
        if not self.lines:
            result = ['| Line | Depth | Level | Entity:User | Time | Content |']
            result.append('|------|-------|-------|-------------|------|---------|')
            if self.filter_applied:
                result.append(f'\n*Filter: {self.filter_applied} | Total: {self.total_count}*')
            return '\n'.join(result)

        # Group lines by table_index (sort first since groupby only groups consecutive elements)

        result = []
        sorted_lines = sorted(self.lines, key=lambda x: x.table_index)
        for table_idx, group_lines in groupby(sorted_lines, key=lambda x: x.table_index):
            lines_list = list(group_lines)

            if result:  # Add separator between tables
                result.append('')
                result.append('---')
                result.append('')

            result.append(f'### Table {table_idx}')
            result.append('')
            result.append('| Line | Depth | Level | Entity:User | Time | Content |')
            result.append('|------|-------|-------|-------------|------|---------|')

            for log_line in lines_list:
                result.append(log_line.to_markdown(max_content_length))

        if self.filter_applied:
            result.append(f'\n*Filter: {self.filter_applied} | Total: {self.total_count}*')

        return '\n'.join(result)

    def to_markdown_summary(self) -> str:
        '''Convert to summary with counts by level.

        Returns:
            Markdown summary string
        '''

        level_counts = Counter(line.level for line in self.lines)
        lines = [
            '## Log Lines Summary',
            f'**Total:** {self.total_count}',
            '',
            '### By Level',
        ]
        for level, count in sorted(level_counts.items()):
            lines.append(f'- {level}: {count}')
        if self.filter_applied:
            lines.append(f'\n*Filter: {self.filter_applied}*')
        return '\n'.join(lines)


class LogHeaderInfo(BaseModel):
    '''Metadata from a single te-log-meta block.'''

    test_id: str
    test_name: str
    entity_type: str
    result: str
    error: str | None = None
    extended_properties: dict[str, str | float] = Field(default_factory=dict)
    start: str
    end: str
    duration: str
    parameters: list[LogParameter] = Field(default_factory=list)
    verdicts: list[LogVerdict] = Field(default_factory=list)
    artifacts: list[LogArtifact] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    objective: str | None = None
    description_url: str | None = None
    description_text: str | None = None


class LogOverview(BaseModel):
    '''Log overview with full metadata and markdown conversion support.'''

    # Identity (from LogEntityModel)
    test_id: str
    test_name: str
    entity_type: str  # test/package/session
    result: str
    error: str | None = None
    extended_properties: dict[str, str | float] = Field(default_factory=dict)

    # Timing (from LogMeta)
    start: str
    end: str
    duration: str

    # Pagination (from LogPagination)
    cur_page: int | None = None
    pages_count: int | None = None

    # Metadata (from LogMeta)
    parameters: list[LogParameter] = Field(default_factory=list)
    verdicts: list[LogVerdict] = Field(default_factory=list)
    artifacts: list[LogArtifact] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)  # Extracted emails
    objective: str | None = None
    description_url: str | None = None
    description_text: str | None = None

    # Additional headers (for multiple te-log-meta blocks)
    additional_headers: list[LogHeaderInfo] = Field(default_factory=list)

    total_lines: int = 0
    level_counts: dict[str, int] = Field(default_factory=dict)
    entity_user_pairs: list[str] = Field(default_factory=list)

    # Optional scenario logs
    scenario_lines: list[LogLine] = Field(default_factory=list)

    def to_markdown(self) -> str:  # noqa: C901
        '''Convert to full markdown document with tables.

        Returns:
            Complete markdown representation of the log overview
        '''
        sections = []

        # Header
        sections.append(f'# Log Overview: {self.test_name}')
        sections.append('')
        sections.append(f'**Result:** {self.result} | **Duration:** {self.duration}')
        sections.append(f'**Start:** {self.start} | **End:** {self.end}')

        if self.error:
            sections.append(f'**Error:** {self.error}')

        # Extended properties (hash, tin)
        if self.extended_properties:
            props = ' | '.join(f'**{k}:** {v}' for k, v in self.extended_properties.items())
            sections.append(props)

        # Pagination
        if self.cur_page is not None and self.pages_count is not None:
            sections.append(f'**Page:** {self.cur_page} of {self.pages_count}')

        # Parameters table
        if self.parameters:
            sections.append('')
            sections.append('## Parameters')
            sections.append('')
            sections.append('| Name | Value |')
            sections.append('|------|-------|')
            for p in self.parameters:
                value = p.value.replace('|', '\\|').replace('\n', ' ')
                sections.append(f'| {p.name} | {value} |')

        # Verdicts
        if self.verdicts:
            sections.append('')
            sections.append('## Verdicts')
            sections.append('')
            for v in self.verdicts:
                level = v.level.root if hasattr(v.level, 'root') else v.level
                sections.append(f'- [{level}] {v.verdict}')

        # Artifacts
        if self.artifacts:
            sections.append('')
            sections.append('## Artifacts')
            sections.append('')
            for a in self.artifacts:
                level = a.level.root if hasattr(a.level, 'root') else a.level
                sections.append(f'- [{level}] {a.artifact}')

        # Requirements
        if self.requirements:
            sections.append('')
            sections.append('## Requirements')
            sections.append('')
            for req in self.requirements:
                sections.append(f'- {req}')

        # Authors
        if self.authors:
            sections.append('')
            sections.append('## Authors')
            sections.append('')
            for author in self.authors:
                sections.append(f'- {author}')

        # Objective
        if self.objective:
            sections.append('')
            sections.append('## Objective')
            sections.append('')
            sections.append(self.objective)

        # Description
        if self.description_text or self.description_url:
            sections.append('')
            sections.append('## Description')
            sections.append('')
            if self.description_text:
                sections.append(self.description_text)
            if self.description_url:
                sections.append(f'[Link]({self.description_url})')

        # Additional headers (for multiple te-log-meta blocks)
        if self.additional_headers:
            sections.append('')
            sections.append('## Additional Test/Package Headers')
            for idx, header in enumerate(self.additional_headers, start=2):
                sections.append('')
                sections.append(f'### Header {idx}: {header.test_name}')
                sections.append(
                    f'**Result:** {header.result} | **Duration:** {header.duration}',
                )
                sections.append(
                    f'**Start:** {header.start} | **End:** {header.end}',
                )
                if header.error:
                    sections.append(f'**Error:** {header.error}')
                if header.extended_properties:
                    props = ' | '.join(
                        f'**{k}:** {v}' for k, v in header.extended_properties.items()
                    )
                    sections.append(props)

        # Statistics
        sections.append('')
        sections.append('## Statistics')
        sections.append('')
        sections.append(f'**Total Lines:** {self.total_lines}')
        if self.level_counts:
            counts = ' | '.join(f'{k}: {v}' for k, v in sorted(self.level_counts.items()))
            sections.append(f'**By Level:** {counts}')

        # Entity:User pairs summary
        if self.entity_user_pairs:
            sections.append('')
            sections.append('### Entity:User Pairs')
            sections.append('')
            # Show first MAX_PAIRS_TO_SHOW pairs max
            pairs_to_show = self.entity_user_pairs[:MAX_PAIRS_TO_SHOW]
            sections.append(', '.join(f'`{p}`' for p in pairs_to_show))
            if len(self.entity_user_pairs) > MAX_PAIRS_TO_SHOW:
                sections.append(
                    f'... and {len(self.entity_user_pairs) - MAX_PAIRS_TO_SHOW} more',
                )

        # Scenario logs
        if self.scenario_lines:
            sections.append('')
            sections.append('## Scenario Logs')

            sorted_scenario = sorted(self.scenario_lines, key=lambda x: x.table_index)
            for table_idx, group_lines in groupby(
                sorted_scenario,
                key=lambda x: x.table_index,
            ):
                lines_list = list(group_lines)
                sections.append('')
                sections.append(f'### Table {table_idx}')
                sections.append('')
                sections.append('| Line | Level | Entity:User | Time | Content |')
                sections.append('|------|-------|-------------|------|---------|')
                for log_line in lines_list:
                    sections.append(log_line.to_markdown(max_content_length=None))

        return '\n'.join(sections)

    def to_markdown_summary(self) -> str:
        '''Convert to brief markdown summary.

        Returns:
            Brief markdown summary
        '''
        sections = [
            f'# {self.test_name}',
            '',
            f'**Result:** {self.result} | **Duration:** {self.duration}',
            f'**Lines:** {self.total_lines}',
        ]
        if self.level_counts:
            counts = ' | '.join(f'{k}: {v}' for k, v in sorted(self.level_counts.items()))
            sections.append(f'**By Level:** {counts}')
        return '\n'.join(sections)
