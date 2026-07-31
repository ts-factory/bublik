# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class MeasurementChartDTO:
    id: int | str
    title: str | None
    subtitle: str
    axis_x: dict[str, Any]
    axis_y: dict[str, Any]
    dataset: list[list[Any]]


@dataclass
class MeasurementDTO:
    run_id: int
    result_id: int
    start: datetime
    test_name: str
    parameters_list: list[str]
    measurement_series_charts: list[MeasurementChartDTO]
