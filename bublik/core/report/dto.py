# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ReportConfigDTO:
    name: str
    description: str
    version: int


@dataclass
class ReportConfigContentDTO:
    config: ReportConfigDTO
    content: dict[str, Any]


@dataclass
class RunReportConfigDTO:
    id: int
    name: str
    version: int
    project: int | None
    description: str


@dataclass
class ReportUnprocessedIterDTO:
    test_name: str
    common_args: dict[str, Any]
    args_vals: dict[str, Any]
    reasons: list[str]


@dataclass
class ReportDTO:
    warnings: list[str]
    config: ReportConfigDTO
    content: list[dict[str, Any]]
    unprocessed_iters: list[ReportUnprocessedIterDTO]
