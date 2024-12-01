# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from references import References
import typing

from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet


__all__ = [
    'OutsideDomainsViewSet',
]


class OutsideDomainsViewSet(RetrieveModelMixin, GenericViewSet):
    filter_backends: typing.ClassVar[list] = []

    def list(self, request):
        return Response(
            {
                'logs': References.logs,
                'revisions': References.revisions,
            },
        )

    @action(detail=False, methods=['get'])
    def logs(self, request):
        return Response({'logs': References.logs})

    @action(detail=False, methods=['get'])
    def revisions(self, request):
        return Response({'revisions': References.revisions})
