# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pydantic import JsonValue


@dataclass
class DashboardRunContextDTO:
    run_id: int
    project_id: int
    project_name: str
    start: float
    status: str | None
    status_by_nok: str
    conclusion: str
    conclusion_reason: str | None


@dataclass
class DashboardRowDTO:
    row_cells: dict[str, JsonValue]
    context: DashboardRunContextDTO


@dataclass
class DashboardColumnDTO:
    key: str
    name: str


@dataclass
class DashboardDTO:
    date: str
    rows: list[DashboardRowDTO]
    header: list[DashboardColumnDTO]
    payload: dict[str, str] = field(default_factory=dict)


@dataclass
class DashboardSettingsValidationDTO:
    valid: bool
    errors: list[str]
