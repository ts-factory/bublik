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
    The selected results of one test, and what a rule for them may contain,
    so that it matches each of the results: any part of the parameters that
    all of the results have, and either no restriction on the verdicts, or
    all the different verdict lists of the results (`verdicts`, in the order
    they first appear, [] for the results without verdicts), for a rule per
    list matching the results that have it.
    """

    test_id: int
    test_name: str
    test_path: str
    results: list[TestIterationResult]
    parameters: dict[str, str]
    verdicts: list[list[str]]
    # The verdicts of each result, in order, by result ID.
    result_verdicts: dict[int, list[str]] = field(default_factory=dict)

    @property
    def result_ids(self) -> list[int]:
        return [result.id for result in self.results]

    def results_with(self, verdicts: list[str] | None) -> list[TestIterationResult]:
        """The results with exactly these verdicts, or all of them for None."""
        if verdicts is None:
            return self.results
        return [
            result for result in self.results if self.result_verdicts[result.id] == verdicts
        ]


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
