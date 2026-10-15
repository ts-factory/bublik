# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings

from bublik.core.server.dto import LatestCommitDTO, RepoRevisionsDTO


class ServerService:
    @staticmethod
    def get_version() -> RepoRevisionsDTO:
        """
        Get server version information.

        Returns:
           DTO object with repository revision information
        """
        rev = settings.REPO_REVISIONS
        commit = rev['latest_commit']

        return RepoRevisionsDTO(
            repo_url=rev['repo_url'],
            repo_branch=rev['repo_branch'],
            repo_tag=rev['repo_tag'],
            latest_commit=LatestCommitDTO(
                commit_date=commit['commit_date'],
                commit_rev=commit['commit_rev'],
                commit_summary=commit['commit_summary'],
            ),
        )
