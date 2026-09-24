# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class UserMcpServerDTO:
    """A user's MCP server without its header values."""

    id: int
    name: str
    slug: str
    url: str
    enabled: bool
    header_names: list[str]
    created: datetime
    updated: datetime
