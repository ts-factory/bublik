# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings


class ServerService:

    @staticmethod
    def get_version() -> dict:
        '''Get server version information.

        Returns:
            Dictionary with repository revision information
        '''
        return settings.REPO_REVISIONS
