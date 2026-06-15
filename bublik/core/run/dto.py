# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime, timedelta


@dataclass
class RunCompromisedDetails:
    status: bool
    comment: str | None = None
    bug_id: str | None = None
    bug_url: str | None = None


@dataclass
class RunRevision:
    name: str
    value: str
    url: str


@dataclass
class RunSpecialCategory:
    name: str
    values: list[str]


@dataclass
class RunDetailsResult:
    project_id: int
    project_name: str
    id: int
    start: datetime | None
    finish: datetime | None
    duration: timedelta | None
    main_package: str | None
    status: str | None
    status_by_nok: str
    compromised: RunCompromisedDetails | None
    conclusion: str
    conclusion_reason: str | None
    important_tags: list[str]
    relevant_tags: list[str]
    branches: list[str]
    revisions: list[RunRevision]
    labels: list[str]
    special_categories: list[RunSpecialCategory]
    configuration: str | None


@dataclass
class RunStatsValues:
    passed: int
    failed: int
    passed_unexpected: int
    failed_unexpected: int
    skipped: int
    skipped_unexpected: int
    abnormal: int


@dataclass
class RunStatsComment:
    comment_id: str
    updated: str
    serial: str
    comment: str


@dataclass
class RunStatsResult:
    result_id: int
    exec_seqno: int
    parent_id: int | None
    type: str
    test_id: int
    test_name: str
    period: str
    path: list[str]
    objective: str
    children: list[RunStatsResult]
    stats: RunStatsValues
    comments: list[RunStatsComment]


@dataclass
class MarkRunCompromisedResult:
    comment: str
    bug: str | None


@dataclass
class RunSummaryStats:
    tests_total: int
    tests_total_plan_percent: int | None
    tests_total_ok: int
    tests_total_ok_percent: int
    tests_total_nok: int
    tests_total_nok_percent: int


@dataclass
class RunSummaryResult:
    id: int
    project_id: int
    project_name: str
    start: datetime | None
    finish: datetime | None
    duration: timedelta | None
    status: str | None
    status_by_nok: str
    compromised: bool | None
    conclusion: str
    conclusion_reason: str | None
    metadata: list[str]
    important_tags: list[str]
    relevant_tags: list[str]
    stats: RunSummaryStats | None


@dataclass
class RunListPagination:
    count: int
    next: str | None
    previous: str | None


@dataclass
class RunListResult:
    pagination: RunListPagination
    results: list[RunSummaryResult]


@dataclass
class RunCommentResult:
    id: int
    comment: str
