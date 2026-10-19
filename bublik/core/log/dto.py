# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pydantic import JsonValue


@dataclass
class LogUrlsDTO:
    url: str | None
    attachments_url: str | None


@dataclass
class LogContentDTO:
    urls: LogUrlsDTO
    log: JsonValue = None
    attachments: JsonValue = None
