# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required
from bublik.core.project import ProjectService
from bublik.data.serializers import ProjectSerializer


class ProjectViewSet(ModelViewSet):
    serializer_class = ProjectSerializer

    def get_queryset(self):
        return ProjectService.list_projects_queryset()

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        project = ProjectService.create_project(request.data)
        serializer = self.get_serializer(project)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def retrieve(self, request, *args, **kwargs):
        project = self.get_object()
        project = ProjectService.get_project_instance(project.id)
        serializer = self.get_serializer(project)
        return Response(serializer.data)

    @auth_required(as_admin=True)
    def update(self, request, *args, **kwargs):
        project = self.get_object()
        project = ProjectService.update_project(
            project_id=project.id,
            data=request.data,
            partial=False,
        )
        serializer = self.get_serializer(project)
        return Response(serializer.data)

    @auth_required(as_admin=True)
    def partial_update(self, request, *args, **kwargs):
        project = self.get_object()
        project = ProjectService.update_project(
            project_id=project.id,
            data=request.data,
            partial=True,
        )
        serializer = self.get_serializer(project)
        return Response(serializer.data)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        ProjectService.delete_project(project.id)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
