# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.exceptions import MethodNotAllowed, ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required
from bublik.data.models import Meta
from bublik.data.serializers import ProjectMetaSerializer


class ProjectViewSet(ModelViewSet):
    serializer_class = ProjectMetaSerializer

    def get_queryset(self):
        return Meta.projects.all().order_by('id')

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        '''
        Create project label meta with passed value.
        Request: POST api/v2/projects.
        '''
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        project_meta, created = serializer.get_or_create()
        return Response(
            self.get_serializer(project_meta).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def update(self, request, *args, **kwargs):
        msg = 'PUT method is not allowed for this resource'
        raise MethodNotAllowed(msg)

    @auth_required(as_admin=True)
    def partial_update(self, request, *args, **kwargs):
        project = self.get_object()
        if project.metaresult_set.exists():
            msg = 'Unable to update: there are runs linked to this project'
            raise ValidationError(msg)
        serializer = self.serializer_class(instance=project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(data=serializer.data)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        if project.metaresult_set.exists():
            msg = 'Unable to delete: there are runs linked to this project'
            raise ValidationError(msg)
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
