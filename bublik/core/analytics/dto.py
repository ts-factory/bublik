# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AnalyticsValueCountDTO:
    value: str
    count: int


@dataclass
class AnalyticsOverviewDTO:
    total_events: int
    page_views: int
    unique_anonymous_users: int
    top_event_names: list[AnalyticsValueCountDTO]
    top_paths: list[AnalyticsValueCountDTO]


@dataclass
class AnalyticsFacetsDTO:
    event_types: list[AnalyticsValueCountDTO]
    event_names: list[AnalyticsValueCountDTO]
    paths: list[AnalyticsValueCountDTO]
    anon_ids: list[AnalyticsValueCountDTO]
    app_versions: list[AnalyticsValueCountDTO]


@dataclass
class AnalyticsChartsDTO:
    top_events_path: str
    top_paths: list[AnalyticsValueCountDTO]
    top_events: list[AnalyticsValueCountDTO]


@dataclass
class AnalyticsPruneResultDTO:
    deleted_by_age: int
    deleted_by_cap: int
    remaining: int
