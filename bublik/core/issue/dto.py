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
