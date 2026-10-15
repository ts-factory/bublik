# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.conf import settings
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.config.services import ConfigServices
from bublik.core.server import ServerService
from bublik.data.models import GlobalConfigs
from bublik.interfaces.api_v2.server.schemas import server_viewset_schema
from bublik.interfaces.api_v2.server.serializers import (
    FeaturesResponseSerializer,
    RepoRevisionsResponseSerializer,
    TabTitlePrefixResponseSerializer,
)


__all__ = [
    'ServerViewSet',
]


@server_viewset_schema
class ServerViewSet(ViewSet):
    filter_backends: typing.ClassVar['list'] = []

    @action(detail=False, methods=['get'])
    def version(self, request):
        data_dto = ServerService.get_version()
        serializer = RepoRevisionsResponseSerializer(instance=data_dto)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def tab_title_prefix(self, _request):
        project_id = self.request.query_params.get('project_id')
        data = {
            'tab_title_prefix': ConfigServices.getattr_from_global(
                GlobalConfigs.PER_CONF.name,
                'TAB_TITLE_PREFIX',
                project_id,
            ),
        }
        serializer = TabTitlePrefixResponseSerializer(instance=data)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def features(self, _request):
        data = {
            'analytics_enabled': settings.ANALYTICS_ENABLED,
            'chat_enabled': settings.AI_CHAT_ENABLED,
        }
        serializer = FeaturesResponseSerializer(instance=data)
        return Response(serializer.data)
