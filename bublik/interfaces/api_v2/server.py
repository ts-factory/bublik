# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.conf import settings
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs


__all__ = [
    'ServerViewSet',
]


class ServerViewSet(RetrieveModelMixin, GenericViewSet):
    filter_backends: typing.ClassVar['list'] = []

    @action(detail=False, methods=['get'])
    def version(self, request):
        data = settings.REPO_REVISIONS
        return Response(data=data)

    @action(detail=False, methods=['get'])
    def tab_title_prefix(self, request):
        project_id = self.request.query_params.get('project_id')
        return Response(
            {
                'tab_title_prefix': ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'TAB_TITLE_PREFIX',
                    project_id,
                ),
            },
        )
