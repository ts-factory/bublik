# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HistoryCountsDTO:
    runs: int
    iterations: int
    total_results: int
    expected_results: int
    unexpected_results: int


@dataclass
class HistoryPaginationDTO:
    page: int
    count: int
    next: str | None
    previous: str | None


@dataclass
class HistoryKeyDTO:
    name: str
    url: str | None


@dataclass
class HistoryExpectedResultDTO:
    result_type: str | None
    verdicts: list[str]
    keys: list[HistoryKeyDTO]


@dataclass
class HistoryObtainedResultDTO:
    result_type: str
    verdicts: list[str]


@dataclass
class HistoryResultDTO:
    start_date: str
    finish_date: str
    duration: str
    obtained_result: HistoryObtainedResultDTO
    expected_results: list[HistoryExpectedResultDTO]
    important_tags: list[str]
    relevant_tags: list[str]
    metadata: list[str]
    parameters: list[str]
    has_error: bool
    has_measurements: bool
    run_id: int
    project_id: int
    project_name: str
    result_id: int
    iteration_id: int
    report_config_id: int | None

    @classmethod
    def from_data(cls, data: dict) -> HistoryResultDTO:
        return cls(
            start_date=data['start_date'],
            finish_date=data['finish_date'],
            duration=data['duration'],
            obtained_result=HistoryObtainedResultDTO(**data['obtained_result']),
            expected_results=[
                HistoryExpectedResultDTO(
                    result_type=expected['result_type'],
                    verdicts=expected['verdicts'],
                    keys=[HistoryKeyDTO(**key) for key in expected['keys']],
                )
                for expected in data['expected_results']
            ],
            important_tags=data['important_tags'],
            relevant_tags=data['relevant_tags'],
            metadata=data['metadata'],
            parameters=list(data['parameters']),
            has_error=data['has_error'],
            has_measurements=data['has_measurements'],
            run_id=data['run_id'],
            project_id=data['project_id'],
            project_name=data['project_name'],
            result_id=data['result_id'],
            iteration_id=data['iteration_id'],
            report_config_id=data['report_config_id'],
        )


@dataclass
class HistoryVerdictResultDTO:
    run_id: int
    result_id: int
    start_date: str
    important_tags: list[str]
    relevant_tags: list[str]


@dataclass
class HistoryVerdictGroupDTO:
    key: str
    result_type: str
    has_error: bool
    verdict: list[str]
    results_data: list[HistoryVerdictResultDTO]


@dataclass
class HistoryIterationGroupDTO:
    hash: str
    iteration_id: int
    parameters: list[str]
    results_by_verdicts: list[HistoryVerdictGroupDTO]

    @classmethod
    def from_data(cls, data: dict) -> HistoryIterationGroupDTO:
        return cls(
            hash=data['hash'],
            iteration_id=data['iteration_id'],
            parameters=list(data['parameters']),
            results_by_verdicts=[
                HistoryVerdictGroupDTO(
                    key=group['key'],
                    result_type=group['result_type'],
                    has_error=group['has_error'],
                    verdict=group['verdict'],
                    results_data=[
                        HistoryVerdictResultDTO(**result) for result in group['results_data']
                    ],
                )
                for group in data['results_by_verdicts']
            ],
        )


@dataclass
class HistoryListDTO:
    from_date: str | None
    to_date: str | None
    counts: HistoryCountsDTO
    pagination: HistoryPaginationDTO
    results: list[HistoryResultDTO]
    results_ids: list[int]


@dataclass
class HistoryGroupedDTO:
    from_date: str | None
    to_date: str | None
    counts: HistoryCountsDTO
    pagination: HistoryPaginationDTO
    results: list[HistoryIterationGroupDTO]
    results_ids: list[int]


@dataclass
class HistoryTagsDTO:
    important: list[str]
    relevant: list[str]
    all: list[str]


@dataclass
class HistoryMetasSearchOptionsDTO:
    tags: HistoryTagsDTO
    branches: list[str]
    revisions: list[str]
    labels: list[str]
