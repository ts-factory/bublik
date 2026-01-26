# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import ValidationError

from bublik.core.exceptions import NotFoundError
from bublik.data import models
from bublik.data.serializers import ProjectSerializer


class ProjectService:
    @staticmethod
    def list_projects_queryset():
        '''
        Get all projects queryset ordered by ID.

        Returns:
            QuerySet of Project objects
        '''
        return models.Project.objects.order_by('id')

    @staticmethod
    def list_projects() -> list[dict]:
        '''
        List all projects.

        Returns:
            List of dictionaries with project id and name
        '''
        return list(ProjectService.list_projects_queryset().values('id', 'name'))

    @staticmethod
    def get_project_instance(project_id: int | str) -> models.Project:
        '''
        Get a single project model instance by ID.

        Args:
            project_id: The ID of the project

        Returns:
            Project model instance

        Raises:
            NotFoundError: if project not found
        '''
        try:
            return models.Project.objects.get(id=project_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Project {project_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def get_project(project_id: int | str) -> dict:
        '''
        Get a single project by ID.

        Args:
            project_id: The ID of the project

        Returns:
            Dictionary with project id and name

        Raises:
            NotFoundError: if project not found
        '''
        project = ProjectService.get_project_instance(project_id)
        return {'id': project.id, 'name': project.name}

    @staticmethod
    def create_project(data: dict) -> models.Project:
        '''
        Create a project.

        Args:
            data: Project payload

        Returns:
            Created Project model instance
        '''
        serializer = ProjectSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    @staticmethod
    def update_project(
        project_id: int | str,
        data: dict,
        partial: bool = False,
    ) -> models.Project:
        '''
        Update a project.

        Args:
            project_id: The ID of the project
            data: Project payload
            partial: Whether to perform partial update

        Returns:
            Updated Project model instance

        Raises:
            NotFoundError: if project not found
        '''
        project = ProjectService.get_project_instance(project_id)
        serializer = ProjectSerializer(project, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    @staticmethod
    def delete_project(project_id: int | str) -> None:
        '''
        Delete a project.

        Args:
            project_id: The ID of the project

        Raises:
            NotFoundError: if project not found
            ValidationError: if project has linked runs
        '''
        project = ProjectService.get_project_instance(project_id)
        if project.testiterationresult_set.exists():
            msg = 'Unable to delete: there are runs linked to this project'
            raise ValidationError(msg)
        project.delete()
