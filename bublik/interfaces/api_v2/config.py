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

from drf_spectacular.utils import (
    extend_schema, 
    extend_schema_view, 
    OpenApiParameter, 
    OpenApiExample, 
    OpenApiResponse,
)
from drf_spectacular.types import OpenApiTypes
from rest_framework import serializers


class ConfigFilterSet(filters.FilterSet):
    '''
    Filters for configurations.

    Available fields for filtering:
    - type: configuration type
    - name: configuration name
    - is_active: configuration activity
    - version: configuration version
    '''
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

# Serialzers for documentation of responses

class ErrorResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    message = serializers.CharField()

class AllVersionsResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    name = serializers.CharField()
    project = serializers.IntegerField()
    all_config_versions = serializers.ListField(child=serializers.DictField())

class AvailableTypesResponseSerializer(serializers.Serializer):
    config_types_names = serializers.ListField(child=serializers.DictField())

@extend_schema_view(
    retrieve=extend_schema(
        summary='Get configuration by ID',
        description='Get detailed information about a specific configuration by its ID.',
        responses={
            200: ConfigSerializer,
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Configuration not found'
            )
        },
        tags=['Configuration'],
    ),
    update=extend_schema(
        summary='Full update configuration',
        description='''
        Fully update a configuration.
        
        Requires administrator's role.
        ''',
        request=ConfigSerializer,
        responses={
            200: ConfigSerializer,
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid request data'
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not enough rights'
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Configuration not found'
            )
        },
        tags=['Configuration'],
    ),
    list=extend_schema(
        summary='List of configurations',
        description='''
        Gets a list of configurations.

        Of all configurations having the same project, type and name:
        - If there are active ones, returns active ones
        - If there are none, returns the latest ones
        ''',
        parameters=[
            OpenApiParameter(
                name='type',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='configuration type',
                examples=[
                    OpenApiExample('Global', value='GLOBAL'),
                    OpenApiExample('Report', value='REPORT'),
                    OpenApiExample('Schedule', value='SCHEDULE'),
                ]
            ),
            OpenApiParameter(
                name='name',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='configuration name'
            ),
            OpenApiParameter(
                name='is_active',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description='congiguration activity'
            ),
            OpenApiParameter(
                name='version',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='configuration version'
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=ConfigSerializer(many=True),
                description='The list of configurations was successfully received'
            )
        },
        tags=['Configuration'],
    ),
    create=extend_schema(
        summary='Create new configuration',
        description='''
        Create new configuration.
        
        Requires adminisrtator's role.
        The configuration will be created if there is no configuration with the same name.
        ''',
        request=ConfigSerializer,
        responses={
            201: ConfigSerializer,
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid request data'
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not enough rights'
            )
        },
        tags=['Configuration'],
    ),
    partial_update=extend_schema(
        summary='Partial update configuration',
        description='''
        Update a configuration or create a new version.

        Rules:
        - If name is provided, the configuration and all its versions
          are renamed
        - If content is not provided and description and/or is_active are provided,
          the configuration is updated accordingly
        - If content is provided:
            - and no version with the same content exists, a new version is created
            - and a version with the same content exists, the existing configuration is updated
        
        Requires administrator's role.
        ''',
        request=ConfigSerializer,
        responses={
            200: ConfigSerializer,
            201: ConfigSerializer,
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid request data'
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not enough rights'
            )
        },
        tags=['Configuration'],
    ),
    destroy=extend_schema(
        summary='Delete configuration',
        description='''
        Deleting a configuration.

        Requires administrator's role.
        ''',
        responses={
            204: OpenApiResponse(description='The configuration was successfully deleted'),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Not enough rights'
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Configuration not found'
            )
        },
        tags=['Configuration'],
    ),
)

class ConfigViewSet(ModelViewSet):
    '''
    API for managing system configurations.
    
    Allows you to create, view, update, and delete configurations,
    and also work with their versions and schemas.
    '''
    queryset = Config.objects.all()
    serializer_class = ConfigSerializer
    filterset_class = ConfigFilter
    filter_backends: typing.ClassVar[list] = [ProjectFilterBackend, DjangoFilterBackend]

    def get_queryset(self):
        '''
        Getting a queryset based on the user's role.

        Returns configurations available to the current user:
        - Administrators see all configurations
        - Regular users can see the configurations of their projects
        '''
        configs = self.filter_queryset(super().get_queryset())
        access_token = self.request.COOKIES.get('access_token')
        user = get_user_by_access_token(access_token)

        not_permission_required_actions_default = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'NOT_PERMISSION_REQUIRED_ACTIONS',
            project_id=None,
            default=[],
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
                default=[],
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
        config = self.get_object()

        with transaction.atomic():
            # rename config and its versions if new name is provided
            if 'name' in request.data:
                new_name = request.data['name']
                serializer = self.get_serializer(config, data={'name': new_name}, partial=True)
                valid_name = serializer.validate_name(new_name)
                Config.objects.get_all_versions(
                    config.type,
                    config.name,
                    config.project,
                ).update(
                    name=valid_name,
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
                return Response(serializer.data, status=status.HTTP_200_OK)

            # if no new content is provided, just update the current config instance
            if 'content' not in update_data:
                serializer = self.get_serializer(config, data=update_data, partial=True)
                serializer.is_valid(raise_exception=True)
                self.perform_update(serializer)
                return Response(serializer.data, status=status.HTTP_200_OK)

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
        return Response(serializer.data, status=status.HTTP_200_OK)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @extend_schema(
        description='Get the JSON schema by passed config type and name.',
        summary='Get configuration schema',
        parameters=[
            OpenApiParameter(
                name='type', 
                type=str, 
                location=OpenApiParameter.QUERY,
                description='configuration type',
                required=True
            ),
            OpenApiParameter(
                name='name', 
                type=str, 
                location=OpenApiParameter.QUERY,
                description='configuration name',
                required=True
            ),
        ],
        responses={
            200: 'Successful receipt of the JSON schema',
            422: 'Invalid configuration type or name',
        },
        tags=['Configuration'],
    )
    @action(detail=False, methods=['get'], url_path='schema')
    def get_schema(self, request, *args, **kwargs):
        config_type = request.query_params.get('type')
        config_name = request.query_params.get('name')
        json_schema = ConfigServices.get_schema(config_type, config_name)
        if json_schema:
            return Response(data=json_schema, status=status.HTTP_200_OK)
        msg = (
            'There is no JSON schema corresponding to the passed '
            f'configuration type-name: {config_type}-{config_name}'
        )
        return Response(
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            data={'type': 'ValueError', 'message': msg},
        )
    
    @extend_schema(
        description='Get all versions of passed configurations.',
        summary='All versions of configuration',
        responses={
            200: 'Successful receipt of the versions list',
            404: 'Configuration not found',
        },
        tags=['Configuration'],
    )
    @action(detail=True, methods=['get'])
    def all_versions(self, request, *args, **kwargs):
        config = self.get_object()
        config_data = self.get_serializer(config).data
        all_config_versions = Config.objects.get_all_versions(
            config_data['type'],
            config_data['name'],
            config_data['project'],
        )

        page = self.paginate_queryset(all_config_versions)
        if page is not None:
            data = {
                'type': config_data['type'],
                'name': config_data['name'],
                'project': config_data['project'],
                'all_config_versions': page,
            }
            return self.get_paginated_response(data)
    
        data = {
            'type': config_data['type'],
            'name': config_data['name'],
            'project': config_data['project'],
            'all_config_versions': all_config_versions,
        }
        return Response(data, status=status.HTTP_200_OK)
    
    @extend_schema(
        summary='Available configuration types and names',
        description='Returns a list of available configuration types and names.',
        responses={200: AvailableTypesResponseSerializer},
        tags=['Configuration'],
    )
    @action(detail=False, methods=['get'])
    def available_types_names(self, request):
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

    def list(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        configs_to_display = (
            queryset
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

        page = self.paginate_queryset(configs_to_display)
        if page is not None:
            return self.get_paginated_response(page)
        
        return Response(configs_to_display, status=status.HTTP_200_OK)
