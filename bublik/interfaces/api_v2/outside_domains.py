# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigNames


__all__ = [
    'OutsideDomainsViewSet',
]


class OutsideDomainsViewSet(RetrieveModelMixin, GenericViewSet):
    filter_backends: typing.ClassVar[list] = []

    def list(self, request):
        return Response(
            {
                'logs': ConfigServices.getattr_from_global(
                    GlobalConfigNames.REFERENCES,
                    'LOGS',
                ),
                'revisions': ConfigServices.getattr_from_global(
                    GlobalConfigNames.REFERENCES,
                    'REVISIONS',
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def logs(self, request):
        return Response(
            {
                'logs': ConfigServices.getattr_from_global(
                    GlobalConfigNames.REFERENCES,
                    'LOGS',
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def revisions(self, request):
        return Response(
            {
                'revisions': ConfigServices.getattr_from_global(
                    GlobalConfigNames.REFERENCES,
                    'REVISIONS',
                ),
            },
        )
