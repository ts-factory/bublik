# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic needs this at runtime.
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RunLeafIdentity(BaseModel):
    model_config = ConfigDict(extra='forbid')

    result_id: int
    run_id: int
    test_name: str
    path: list[str]


class RunLeafPagination(BaseModel):
    model_config = ConfigDict(extra='forbid')

    page: int = Field(ge=1)
    count: int = Field(ge=0)
    next: str | None
    previous: str | None


class RunExpectedKey(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    url: str | None


class RunExpectedResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    result_type: str
    verdicts: list[str]
    keys: list[RunExpectedKey]


class RunObtainedResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    result_type: str | None
    verdicts: list[str]


class RunLeafResultIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')

    issue_id: int
    issue_title: str
    issue_state: str
    bug_key: str | None
    bug_url: str | None
    category: str
    expected: bool | None
    rule_result_id: int
    rule_id: int
    origin: str


class RunLeafResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
    path: str | None
    result_id: int
    run_id: int
    project_id: int
    project_name: str
    iteration_id: int
    start: datetime
    obtained_result: RunObtainedResult
    expected_results: list[RunExpectedResult]
    artifacts: list[str]
    parameters: list[str]
    comments: list[str]
    requirements: list[str]
    has_error: bool
    has_measurements: bool
    issues: list[RunLeafResultIssue]
    effective_expected: bool
    classification: Literal['expected', 'unexpected']


class RunLeafResultsPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    leaf: RunLeafIdentity
    requirements: str | None
    pagination: RunLeafPagination
    results: list[RunLeafResult]
