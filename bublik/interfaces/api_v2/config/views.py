# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import typing

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required, get_request_user
from bublik.core.config.filters import ConfigFilter
from bublik.core.config.management import ConfigManagementService
from bublik.core.config.services import ConfigServices
from bublik.core.exceptions import UnprocessableEntityError
from bublik.core.filter_backends import ProjectFilterBackend
from bublik.data.models import Config
from bublik.data.serializers import ConfigSerializer
from bublik.interfaces.api_v2.config.schemas import config_viewset_schema


@config_viewset_schema
class ConfigViewSet(ModelViewSet):
    """
    API for managing system configurations.
    """

    pagination_class = None
    queryset = Config.objects.all()
    serializer_class = ConfigSerializer
    filterset_class = ConfigFilter
    filter_backends: typing.ClassVar[list] = [ProjectFilterBackend, DjangoFilterBackend]
    http_method_names: typing.ClassVar[list[str]] = [
        'get',
        'post',
        'patch',
        'delete',
        'head',
        'options',
    ]

    def get_queryset(self):
        return ConfigManagementService.visible_for(
            get_request_user(self.request),
            self.filter_queryset(super().get_queryset()),
        )

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        config = ConfigManagementService.create(request.data, get_request_user(request))
        config_data = self.get_serializer(config).data
        return Response(config_data, status=status.HTTP_201_CREATED)

    @auth_required(as_admin=True)
    def partial_update(self, request, *args, **kwargs):
        config, created = ConfigManagementService.update(
            self.get_object(),
            request.data,
            get_request_user(request),
        )
        config_data = self.get_serializer(config).data
        if created:
            return Response(config_data, status=status.HTTP_201_CREATED)
        return Response(config_data)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        ConfigManagementService.delete(self.get_object(), get_request_user(request))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='schema')
    def get_schema(self, request, *args, **kwargs):
        config_type = request.query_params.get('type')
        config_name = request.query_params.get('name')
        json_schema = ConfigServices.get_schema(config_type, config_name)
        if json_schema:
            return Response(data=json_schema)
        msg = (
            'There is no JSON schema corresponding to the passed '
            f'configuration type-name: {config_type}-{config_name}'
        )
        raise UnprocessableEntityError(msg)

    @action(detail=True, methods=['get'])
    def all_versions(self, request, *args, **kwargs):
        config = self.get_object()
        config_data = self.get_serializer(config).data
        all_config_versions = Config.objects.get_all_versions(
            config_data['type'],
            config_data['name'],
            config_data['project'],
        )

        data = {
            'type': config_data['type'],
            'name': config_data['name'],
            'project': config_data['project'],
            'all_config_versions': all_config_versions,
        }
        return Response(data)

    @action(detail=False, methods=['get'])
    def available_types_names(self, request):
        return Response(
            {'config_types_names': ConfigManagementService.available_types_names()},
        )

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        return Response(ConfigManagementService.summarize(queryset))
