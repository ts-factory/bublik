# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from dataclasses import dataclass


@dataclass
class ProjectDTO:
    id: int
    name: str
