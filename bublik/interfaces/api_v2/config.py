# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import logging
import re

import per_conf

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from bublik.core.auth import auth_required
from bublik.core.config.filters import ConfigFilter
from bublik.core.queries import get_or_none
from bublik.data.models import Config
from bublik.data.serializers import ConfigSerializer


logger = logging.getLogger('')


class ConfigViewSet(ModelViewSet):
    queryset = Config.objects.all()
    serializer_class = ConfigSerializer

    def filter_queryset(self, queryset):
        return ConfigFilter(queryset=self.queryset).qs

    def get_or_create(self, data):
        serializer = self.get_serializer(data=data)
        serializer.update_data()
        serializer.is_valid(raise_exception=True)
        return serializer.get_or_create(serializer.validated_data)

    @auth_required(as_admin=True)
    @action(detail=False, methods=['get'])
    def create_by_per_conf(self, request, *args, **kwargs):
        '''
        Create global config object by per_conf.py (if it does not exist).
        Return a created object or an existing per_conf global config object.
        Request: GET api/v2/config/create_by_per_conf.
        '''
        global_config = get_or_none(
            Config.objects,
            type='global',
            name='per_conf',
        )
        if global_config:
            return Response(
                self.get_serializer(global_config).data,
                status=status.HTTP_302_FOUND,
            )

        # convert per_conf.py into dict
        per_conf_dict = {}
        args = dir(per_conf)
        args = [arg for arg in args if not re.fullmatch(r'__.+?__', arg)]
        per_conf_dict = {arg: getattr(per_conf, arg, None) for arg in args}

        # convert tuple into list
        if 'RUN_STATUS_BY_NOK_BORDERS' in per_conf_dict:
            per_conf_dict['RUN_STATUS_BY_NOK_BORDERS'] = list(
                per_conf_dict['RUN_STATUS_BY_NOK_BORDERS'],
            )

        data = {
            'type': 'global',
            'name': 'per_conf',
            'description': 'The main project config',
            'content': per_conf_dict,
        }
        config, created = self.get_or_create(data)
        config_data = self.get_serializer(config).data
        if not created:
            return Response(config_data, status=status.HTTP_302_FOUND)
        return Response(config_data, status=status.HTTP_201_CREATED)

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
            if k in ['type', 'name', 'description', 'content']
        }
        config, created = self.get_or_create(data)
        config_data = self.get_serializer(config).data
        if not created:
            return Response(config_data, status=status.HTTP_302_FOUND)
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
        updated_data = {'type': config_data['type'], 'name': config_data['name']}
        for attr in ['description', 'content']:
            updated_data[attr] = (
                request.data[attr] if attr in request.data else config_data[attr]
            )

        if 'content' in request.data:
            # create new object version
            new_config, created = self.get_or_create(updated_data)
            new_config_data = self.get_serializer(new_config).data
            if not created:
                return Response(new_config_data, status=status.HTTP_302_FOUND)
            return Response(new_config_data, status=status.HTTP_201_CREATED)

        if 'description' in request.data:
            # update object with passed description
            serializer = self.get_serializer(config, data=updated_data, partial=True)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(serializer.data, status=status.HTTP_200_OK)

        return Response(config_data, status=status.HTTP_200_OK)

    @auth_required(as_admin=True)
    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @auth_required(as_admin=True)
    @action(detail=True, methods=['get'])
    def all_versions(self, request, *args, **kwargs):
        '''
        Get all versions of passed config.
        Request: GET api/v2/config/<ID>/all_versions.
        '''
        config = self.get_object()
        config_data = self.get_serializer(config).data

        all_config_versions = Config.objects.filter(
            type=config_data['type'],
            name=config_data['name'],
        ).values('id', 'is_current', 'description', 'created')

        data = {
            'type': config_data['type'],
            'name': config_data['name'],
            'all_config_versions': all_config_versions,
        }
        return Response(data, status=status.HTTP_200_OK)

    @auth_required(as_admin=True)
    @action(detail=True, methods=['get'])
    def mark_as_current(self, request, *args, **kwargs):
        '''
        Make passed config version current.
        Request: GET api/v2/config/<ID>/mark_as_current.
        '''
        config = self.get_object()
        config_data = self.get_serializer(config).data

        current = Config.get_current_version(config_data['type'], config_data['name'])
        current.is_current = False
        current.save()

        config.is_current = True
        config.save()

        return Response(status=status.HTTP_204_NO_CONTENT)

    @auth_required(as_admin=True)
    def list(self, request):
        current_configs = Config.objects.filter(is_current=True).values(
            'id',
            'type',
            'name',
            'description',
            'created',
        )
        return Response(current_configs, status=status.HTTP_200_OK)

    @auth_required(as_admin=True)
    def retrieve(self, request, pk=None):
        return super().retrieve(request)
