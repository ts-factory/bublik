# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class UserTokenDTO:
    """A token without its value or hash."""

    id: int
    name: str
    prefix: str
    status: str
    owner: str
    created: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None


@dataclass
class IssuedUserTokenDTO:
    """A newly issued token together with its value."""

    token: UserTokenDTO
    value: str
