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
    def project(self, request):
        # TODO: The existence of PROJECT in per_conf should be checked when the server starts
        return Response(
            {
                'project': ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'PROJECT',
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def version(self, request):
        data = settings.REPO_REVISIONS
        return Response(data=data)
