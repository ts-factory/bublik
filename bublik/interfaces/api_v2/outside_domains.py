# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs


__all__ = [
    'OutsideDomainsViewSet',
]


class OutsideDomainsViewSet(RetrieveModelMixin, GenericViewSet):
    filter_backends: typing.ClassVar[list] = []

    def list(self, request):
        project_id = request.query_params.get('project')
        return Response(
            {
                'logs': ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'LOGS_BASES',
                    project_id,
                ),
                'issues': ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'ISSUES',
                    project_id,
                ),
                'revisions': ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'REVISIONS',
                    project_id,
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def logs(self, request):
        project_id = request.query_params.get('project')
        return Response(
            {
                'logs': ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'LOGS_BASES',
                    project_id,
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def issues(self, request):
        project_id = request.query_params.get('project')
        return Response(
            {
                'issues': ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'ISSUES',
                    project_id,
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def revisions(self, request):
        project_id = request.query_params.get('project')
        return Response(
            {
                'revisions': ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'REVISIONS',
                    project_id,
                ),
            },
        )
