# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import per_conf

from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.settings import REPO_REVISIONS


__all__ = [
    'ServerViewSet',
]


class ServerViewSet(RetrieveModelMixin, GenericViewSet):
    filter_backends = []

    @action(detail=False, methods=['get'])
    def project(self, request):
        # TODO: The existence of PROJECT in per_conf should be checked when the server starts
        return Response({'project': per_conf.PROJECT})

    @action(detail=False, methods=['get'])
    def version(self, request):
        data = REPO_REVISIONS
        return Response(data=data)
