# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required
from bublik.data.models import Project
from bublik.data.serializers import ProjectSerializer


class ProjectViewSet(ModelViewSet):
    queryset = Project.objects.all().order_by('id')
    serializer_class = ProjectSerializer

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        return super().create(request, *args, **kwargs)

    @auth_required(as_admin=True)
    def update(self, request, *args, **kwargs):
        project = self.get_object()
        if project.testiterationresult_set.exists():
            msg = 'Unable to rename: there are runs linked to this project'
            raise ValidationError(msg)
        return super().update(request, *args, **kwargs)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        project = self.get_object()
        if project.testiterationresult_set.exists():
            msg = 'Unable to delete: there are runs linked to this project'
            raise ValidationError(msg)
        return super().destroy(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
