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


class RunLeafResult(BaseModel):
    model_config = ConfigDict(extra='forbid')

    name: str
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
    classification: Literal['expected', 'unexpected']


class RunLeafResultsPayload(BaseModel):
    model_config = ConfigDict(extra='forbid')

    leaf: RunLeafIdentity
    requirements: str | None
    pagination: RunLeafPagination
    results: list[RunLeafResult]
