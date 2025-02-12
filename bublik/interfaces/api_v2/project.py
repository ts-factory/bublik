# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required
from bublik.data.models import Meta
from bublik.data.serializers import MetaSerializer


class ProjectViewSet(ModelViewSet):
    serializer_class = MetaSerializer

    def get_queryset(self):
        return Meta.projects

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        '''
        Create project label meta with passed value.
        Request: POST api/v2/project.
        '''
        project_meta, created = MetaSerializer.get_or_create_project(request.data['value'])
        project_meta_data = self.get_serializer(project_meta).data
        if not created:
            return Response(project_meta_data, status=status.HTTP_400_BAD_REQUEST)
        return Response(project_meta_data, status=status.HTTP_201_CREATED)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    def list(self, request):
        all_projects = (
            self.get_queryset()
            .order_by('id')
            .values(
                'id',
                'value',
            )
        )
        return Response(all_projects, status=status.HTTP_200_OK)
