# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from bublik.data.models import TestIterationResult


@dataclass
class ClassifyTestDTO:
    """
    The selected results of one test, and what a rule for them may contain:
    the parameters and the verdicts that all of the results have, so that a
    rule of any part of them matches each of the results.
    """

    test_id: int
    test_name: str
    test_path: str
    results: list[TestIterationResult]
    parameters: dict[str, str]
    verdicts: list[str]

    @property
    def result_ids(self) -> list[int]:
        return [result.id for result in self.results]


@dataclass
class ClassifyOptionsDTO:
    """
    What the selected results allow a rule to contain. The tags are those of
    the run: a rule may contain any of the important and relevant ones, and
    usually contains the important ones.
    """

    run_id: int
    tags: dict[str, list[str]]
    tests: list[ClassifyTestDTO] = field(default_factory=list)


@dataclass
class UnclassifyDTO:
    """
    What taking a classification off results did: the stamps deleted, the
    rules left without stamps that were deleted, and the issues left without
    rules that were deleted.
    """

    deleted_rule_result_ids: list[int] = field(default_factory=list)
    deleted_rule_ids: list[int] = field(default_factory=list)
    deleted_issue_ids: list[int] = field(default_factory=list)
