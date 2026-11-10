# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BulkActionDTO:
    """
    What a bulk action over issues or rules did: the IDs it was given, split
    by outcome, each list in the order requested.
    """

    requested: int = 0
    updated: list[int] = field(default_factory=list)
    unchanged: list[int] = field(default_factory=list)
    not_found: list[int] = field(default_factory=list)

    @classmethod
    def split(cls, ids, existing_ids, updated_ids) -> BulkActionDTO:
        updated_ids = set(updated_ids)
        return cls(
            requested=len(ids),
            updated=[i for i in ids if i in updated_ids],
            unchanged=[i for i in ids if i in existing_ids and i not in updated_ids],
            not_found=[i for i in ids if i not in existing_ids],
        )


@dataclass
class IssueFacetsDTO:
    """
    How many issues each value of a list filter would leave, the other
    filters applied: by state, by category (issues having an active rule of
    it), by rules state and by project ID.
    """

    state: dict[str, int] = field(default_factory=dict)
    categories: dict[str, int] = field(default_factory=dict)
    rules: dict[str, int] = field(default_factory=dict)
    project: dict[int, int] = field(default_factory=dict)


@dataclass
class IssueRuleFacetsDTO:
    """
    How many rules each value of a list filter would leave, the other
    filters applied. The matcher values are spelled as their filters take
    them: a tag as it is, a parameter as 'name=value', the verdicts as a
    JSON list, or null for the rules ignoring them.
    """

    active: dict[str, int] = field(default_factory=dict)
    category: dict[str, int] = field(default_factory=dict)
    expected: dict[str, int] = field(default_factory=dict)
    project: dict[int, int] = field(default_factory=dict)
    issue_state: dict[str, int] = field(default_factory=dict)
    tag: dict[str, int] = field(default_factory=dict)
    verdicts: dict[str, int] = field(default_factory=dict)
    parameter: dict[str, int] = field(default_factory=dict)


@dataclass
class TestOptionDTO:
    """A test a rule may belong to, with the path that tells it from its namesakes."""

    id: int
    name: str
    path: str
