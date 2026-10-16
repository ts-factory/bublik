# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class TreeNodeDTO:
    id: int
    start: datetime
    name: str | None
    entity: str | None
    has_error: bool
    skipped: bool
    children: list[int] | None = None


@dataclass
class TreeDTO:
    tree: dict[int, TreeNodeDTO]
    main_package: int | None
