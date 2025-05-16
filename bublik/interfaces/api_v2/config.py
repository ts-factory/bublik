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
        Create config with passed type, name, description and content
        (if object with the same content does not exist).
        Return a created object or an existing object with the passed content.
        Request: POST api/v2/config.
        '''
        data = {
            k: v
            for k, v in request.data.items()
            if k in ['type', 'name', 'is_active', 'description', 'content']
        }
        config, created = self.serializer_class.validate_and_get_or_create(
            config_data=data,
            access_token=request.COOKIES.get('access_token'),
        )
        config_data = self.get_serializer(config).data
        if not created:
            return Response(config_data, status=status.HTTP_400_BAD_REQUEST)
        return Response(config_data, status=status.HTTP_201_CREATED)

    @auth_required(as_admin=True)
    def partial_update(self, request, *args, **kwargs):
        '''
        Update or create new version of the config by changing its description or content.
        Request: PATCH api/v2/config/<ID>.
        '''
        config = self.get_object()

        # prepare data for updating/creating a new version
        config_data = self.get_serializer(config).data
        updated_data = {
            'type': config_data['type'],
        }
        for attr in ['name', 'description', 'is_active', 'content']:
            updated_data[attr] = (
                request.data[attr] if attr in request.data else config_data[attr]
            )

        if 'name' in request.data:
            # check the passed name for uniqueness
            same_name_configs = Config.objects.filter(
                type=updated_data['type'],
                name=updated_data['name'],
            )
            if same_name_configs:
                msg = f'A {updated_data["type"]} configuration with the same name already exist'
                data = {
                    attr: updated_data[attr]
                    for attr in ['type', 'name', 'description', 'content']
                }
                return Response(
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    data={
                        'type': 'ValueError',
                        'message': msg,
                        'new_config_data': data,
                    },
                )
            if 'content' not in request.data:
                # rename all config versions
                serializer = self.get_serializer(config, data=updated_data, partial=True)
                serializer.validate_name(updated_data['name'])
                Config.objects.get_all_versions(
                    config_data['type'],
                    config_data['name'],
                ).update(
                    name=updated_data['name'],
                )

        if 'content' in request.data:
            # create new object version
            new_config, created = self.serializer_class.validate_and_get_or_create(
                config_data=updated_data,
                access_token=request.COOKIES.get('access_token'),
            )
            new_config_data = self.get_serializer(new_config).data
            if not created:
                return Response(new_config_data, status=status.HTTP_400_BAD_REQUEST)
            return Response(new_config_data, status=status.HTTP_201_CREATED)

        serializer = self.get_serializer(config, data=updated_data, partial=True)
        serializer.is_valid(raise_exception=True)

        if 'is_active' in request.data and updated_data['is_active']:
            config.activate()

        self.perform_update(serializer)

        return Response(serializer.data, status=status.HTTP_200_OK)

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
        )

        data = {
            'type': config_data['type'],
            'name': config_data['name'],
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
        Of all configurations having the same type and name, if there are active ones,
        returns active ones, if there are none, returns the latest ones.
        '''
        configs_to_display = (
            Config.objects.order_by('type', 'name', '-is_active', '-created')
            .distinct('type', 'name')
            .values(
                'id',
                'version',
                'is_active',
                'type',
                'name',
                'description',
                'created',
            )
        )
        return Response(configs_to_display, status=status.HTTP_200_OK)

    @check_action_permission('read_configs')
    def retrieve(self, request, pk=None):
        return super().retrieve(request)
