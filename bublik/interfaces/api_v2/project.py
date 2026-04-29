# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required
from bublik.core.project import ProjectBadgeService, ProjectService
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

    @action(detail=True, methods=['get'], url_path='badge')
    def badge(self, request, pk=None):
        '''
        SVG badge for the latest run of a project.

        Query parameters:
            label   (optional) -- left-side text; defaults to project name
            metric  (optional) -- passed | unexpected | total | rate
                                   default shows run conclusion with nok count
        '''
        project = self.get_object()
        label = request.query_params.get('label') or project.name
        metric = request.query_params.get('metric', '').lower()
        run = ProjectBadgeService.get_latest_run(project)
        value, color = ProjectBadgeService.badge_content(run, metric)
        svg = ProjectBadgeService.render(label, value, color)
        return HttpResponse(svg, content_type='image/svg+xml')
