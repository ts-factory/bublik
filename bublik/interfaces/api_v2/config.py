# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import typing

from django.contrib.postgres.fields import JSONField
from django.db import transaction
from django_filters import rest_framework as filters
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required, get_user_by_access_token
from bublik.core.config.filters import ConfigFilter
from bublik.core.config.services import ConfigServices
from bublik.core.filter_backends import ProjectFilterBackend
from bublik.core.shortcuts import serialize
from bublik.data.models import Config, ConfigTypes, GlobalConfigs, Project, UserRoles
from bublik.data.serializers import ConfigSerializer


class ConfigFilterSet(filters.FilterSet):
    class Meta:
        model = Config
        fields: typing.ClassVar = ['type', 'name', 'is_active', 'version']
        filter_overrides: typing.ClassVar = {
            JSONField: {
                'filter_class': filters.CharFilter,
                'extra': lambda f: {
                    'lookup_expr': 'icontains',
                },
            },
        }


class ConfigViewSet(ModelViewSet):
    pagination_class = None
    queryset = Config.objects.all()
    serializer_class = ConfigSerializer
    filterset_class = ConfigFilter
    filter_backends: typing.ClassVar[list] = [ProjectFilterBackend, DjangoFilterBackend]

    def get_queryset(self):
        configs = self.filter_queryset(super().get_queryset())
        access_token = self.request.COOKIES.get('access_token')
        user = get_user_by_access_token(access_token)

        not_permission_required_actions_default = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'NOT_PERMISSION_REQUIRED_ACTIONS',
            project_id=None,
        )
        if (
            user and UserRoles.ADMIN in user.roles
        ) or 'read_configs' in not_permission_required_actions_default:
            return configs | Config.objects.filter(project__isnull=True)

        project_ids = list(Project.objects.all().values_list('id', flat=True))
        project_ids = [
            project_id
            for project_id in project_ids
            if 'read_configs'
            in ConfigServices.getattr_from_global(
                GlobalConfigs.PER_CONF.name,
                'NOT_PERMISSION_REQUIRED_ACTIONS',
                project_id=project_id,
            )
        ]
        if project_ids:
            configs = configs.filter(project_id__in=project_ids)
            if configs:
                return configs | Config.objects.filter(project__isnull=True)

        return configs.none()

    @auth_required()
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @auth_required(as_admin=True)
    def update(self, request, *args, **kwargs):
        return super().update(request, *args, **kwargs)

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        '''
        Create and return a config unless another with the same name exists.
        Request: POST api/v2/config.
        '''
        access_token = request.COOKIES.get('access_token')
        serializer = serialize(
            self.serializer_class,
            data=request.data,
            context={'access_token': access_token},
        )
        config, _ = serializer.get_or_create()
        config_data = self.get_serializer(config).data
        return Response(config_data, status=status.HTTP_201_CREATED)

    @auth_required(as_admin=True)
    def partial_update(self, request, *args, **kwargs):
        '''
        Update a configuration or create a new version.

        Rules:
        - If name is provided, the configuration and all its versions
          are renamed.
        - If content is not provided and description and/or is_active are provided,
          the configuration is updated accordingly.
        - If content is provided:
            - and no version with the same content exists, a new version is created.
            - and a version with the same content exists, the existing configuration is updated.

        Request: PATCH api/v2/config/<ID>.
        '''
        config = self.get_object()

        with transaction.atomic():
            # rename config and its versions if new name is provided
            if 'name' in request.data:
                new_name = request.data['name']
                serializer = self.get_serializer(config, data={'name': new_name}, partial=True)
                serializer.is_valid(raise_exception=True)
                Config.objects.get_all_versions(
                    config.type,
                    config.name,
                    config.project,
                ).update(
                    name=serializer.validated_data['name'],
                )
                config.refresh_from_db()

            # collect data for update
            update_data = {
                k: v
                for k, v in request.data.items()
                if k in ['description', 'is_active', 'content']
            }

            if not update_data:
                serializer = self.get_serializer(config)
                return Response(serializer.data)

            # if no new content is provided, just update the current config instance
            if 'content' not in update_data:
                serializer = self.get_serializer(config, data=update_data, partial=True)
                serializer.is_valid(raise_exception=True)
                self.perform_update(serializer)
                return Response(serializer.data)

        # create a new config version if the provided content differs from all existing versions
        access_token = request.COOKIES.get('access_token')
        serializer = self.get_serializer(
            config,
            data=update_data,
            partial=True,
            context={'access_token': access_token},
        )
        serializer.is_valid(raise_exception=True)
        updated_config, created = serializer.get_or_create()
        if created:
            updated_config_data = self.get_serializer(updated_config).data
            return Response(updated_config_data, status=status.HTTP_201_CREATED)

        # update the existing config matching the provided content:
        # set is_active and description to the provided values,
        # or to the corresponding values from the current config if not provided
        update_data['is_active'] = update_data.get('is_active', config.is_active)
        update_data['description'] = update_data.get('description', config.description)
        serializer = self.get_serializer(updated_config, data=update_data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=['get'], url_path='schema')
    def get_schema(self, request, *args, **kwargs):
        '''
        Get the JSON schema by passed config type and name.
        Request: GET api/v2/config/get_schema/?type=<config_type>&name=<config_name>.
        '''
        config_type = request.query_params.get('type')
        config_name = request.query_params.get('name')
        json_schema = ConfigServices.get_schema(config_type, config_name)
        if json_schema:
            return Response(data=json_schema)
        msg = (
            'There is no JSON schema corresponding to the passed '
            f'configuration type-name: {config_type}-{config_name}'
        )
        return Response(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            data={'type': 'ValueError', 'message': msg},
        )

    @action(detail=True, methods=['get'])
    def all_versions(self, request, *args, **kwargs):
        '''
        Get all versions of passed config.
        Request: GET api/v2/config/<ID>/all_versions.
        '''
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
        '''
        Returns a list of available configuration types and names.
        '''
        config_type_names = [
            {
                'type': ConfigTypes.REPORT,
                'required': False,
                'description': 'Configuration for report generation',
            },
            {
                'type': ConfigTypes.SCHEDULE,
                'required': False,
                'description': 'Schedule of the runs to be made',
            },
        ]
        for global_config in GlobalConfigs:
            config_type_names.append(
                {
                    'type': ConfigTypes.GLOBAL,
                    'name': global_config.name,
                    'required': global_config in GlobalConfigs.required(),
                    'description': global_config.description,
                },
            )
        return Response({'config_types_names': config_type_names})

    def list(self, request):
        '''
        Of all configurations having the same project, type and name, if there are active ones,
        returns active ones, if there are none, returns the latest ones.
        '''
        configs_to_display = (
            self.get_queryset()
            .order_by('project', 'type', 'name', '-is_active', '-created')
            .distinct('project', 'type', 'name')
            .values(
                'id',
                'version',
                'is_active',
                'type',
                'name',
                'description',
                'project',
                'created',
            )
        )
        return Response(configs_to_display)
