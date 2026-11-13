# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LatestCommitDTO:
    commit_date: str
    commit_rev: str
    commit_summary: str


@dataclass
class RepoRevisionsDTO:
    repo_url: str
    repo_branch: str
    latest_commit: LatestCommitDTO
    repo_tag: str
