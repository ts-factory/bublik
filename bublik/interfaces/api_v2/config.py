# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import typing

from django.contrib.postgres.fields import JSONField
from django_filters import rest_framework as filters
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required, check_action_permission
from bublik.core.config.filters import ConfigFilter
from bublik.core.config.services import ConfigServices
from bublik.core.shortcuts import serialize
from bublik.data.models import Config, ConfigTypes, GlobalConfigs
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
    queryset = Config.objects.all()
    serializer_class = ConfigSerializer
    filterset_class = ConfigFilterSet

    def filter_queryset(self, queryset):
        return ConfigFilter(queryset=self.queryset).qs

    @auth_required(as_admin=True)
    def create(self, request, *args, **kwargs):
        '''
        Create config with passed data (if object with the same content does not exist).
        Return a created object or an existing object with the passed content.
        Request: POST api/v2/config.
        '''
        access_token = request.COOKIES.get('access_token')
        serializer = serialize(
            self.serializer_class,
            data=request.data,
            context={'access_token': access_token},
        )
        config, created = serializer.get_or_create()
        config_data = self.get_serializer(config).data
        if not created:
            return Response(config_data, status=status.HTTP_400_BAD_REQUEST)
        return Response(config_data, status=status.HTTP_201_CREATED)

    @auth_required(as_admin=True)
    def partial_update(self, request, *args, **kwargs):
        '''
        Update a configuration or create a new version.

        - If both content and name are provided, a new configuration is created.
        - If content is provided without a name, a new version is created.
        - If name is provided without content, the configuration and all its versions
          are renamed.
        - If description and is_active are provided, the configuration is updated.

        Request: PATCH api/v2/config/<ID>.
        '''
        config = self.get_object()

        update_data = {
            **request.data,
            'type': config.type,
            'project': config.project.id if config.project else None,
        }
        update_serializer = self.get_serializer(config, data=update_data, partial=True)
        update_serializer.is_valid(raise_exception=True)

        if 'content' not in request.data:
            if 'name' in request.data:
                # rename all config versions
                Config.objects.get_all_versions(
                    config.type,
                    config.name,
                    config.project,
                ).update(
                    name=update_serializer.validated_data['name'],
                )
            # update config
            self.perform_update(update_serializer)
            return Response(update_serializer.data, status=status.HTTP_200_OK)

        # create new object version
        access_token = request.COOKIES.get('access_token')
        create_data = {**self.get_serializer(config).data, **update_data}
        serializer = serialize(
            self.serializer_class,
            data=create_data,
            context={'access_token': access_token},
        )
        new_config, created = serializer.get_or_create()
        new_config_data = self.get_serializer(new_config).data
        if not created:
            return Response(new_config_data, status=status.HTTP_400_BAD_REQUEST)
        return Response(new_config_data, status=status.HTTP_201_CREATED)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @check_action_permission('read_configs')
    @action(detail=False, methods=['get'], url_path='schema')
    def get_schema(self, request, *args, **kwargs):
        '''
        Get the JSON schema by passed config type and name.
        Request: GET api/v2/config/get_schema/?type=<config_type>&name=<config_name>.
        '''
        config_type = request.query_params.get('type')
        config_name = request.query_params.get('name')
        serializer = self.get_serializer(data={'type': config_type, 'name': config_name})
        config_type = serializer.validate_type(config_type)
        config_name = serializer.validate_name(config_name)
        json_schema = ConfigServices.get_schema(config_type, config_name)
        if json_schema:
            return Response(data=json_schema, status=status.HTTP_200_OK)
        msg = 'There is no JSON schema corresponding to the passed configuration type and name'
        return Response(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            data={'type': 'ValueError', 'message': msg},
        )

    @check_action_permission('read_configs')
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
        return Response(data, status=status.HTTP_200_OK)

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
        return Response({'config_types_names': config_type_names}, status=status.HTTP_200_OK)

    @check_action_permission('read_configs')
    def list(self, request):
        '''
        Of all configurations having the same project, type and name, if there are active ones,
        returns active ones, if there are none, returns the latest ones.
        '''
        configs_to_display = (
            Config.objects.order_by('project', 'type', 'name', '-is_active', '-created')
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
        return Response(configs_to_display, status=status.HTTP_200_OK)

    @check_action_permission('read_configs')
    def retrieve(self, request, pk=None):
        return super().retrieve(request)
