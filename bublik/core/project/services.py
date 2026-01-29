# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.core.exceptions import ValidationError

from bublik.data import models


class ProjectService:
    '''Service for project-related operations (shared between REST API and MCP).'''

    @staticmethod
    def list_projects() -> list[dict]:
        '''List all projects.

        Returns:
            List of dictionaries with project id and name
        '''
        return list(models.Project.objects.values('id', 'name').order_by('id'))

    @staticmethod
    def get_project(project_id: int) -> dict:
        '''Get a single project by ID.

        Args:
            project_id: The ID of the project

        Returns:
            Dictionary with project id and name

        Raises:
            ValidationError: if project not found
        '''
        try:
            project = models.Project.objects.values('id', 'name').get(id=project_id)
        except models.Project.DoesNotExist as e:
            msg = f'Project {project_id} not found'
            raise ValidationError(msg) from e
        return project
