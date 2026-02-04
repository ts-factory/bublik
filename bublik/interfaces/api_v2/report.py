# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.report.services import ReportService
from bublik.data.models import TestIterationResult
from bublik.data.serializers import TestIterationResultSerializer


__all__ = [
    'ReportViewSet',
]


class ReportViewSet(RetrieveModelMixin, GenericViewSet):
    queryset = TestIterationResult.objects.all()
    serializer_class = TestIterationResultSerializer

    @action(detail=True, methods=['get'])
    def configs(self, request, pk=None):
        '''
        Return a list of active configs that can be used to build a report on the current run.
        Request: GET /api/v2/report/<run_id>/configs
        '''
        return Response(
            {'run_report_configs': ReportService.get_configs_for_run_report(self.get_object())},
        )

    def retrieve(self, request, pk=None):
        '''
        Request: GET /api/v2/report/<run_id>?config=<config_id\\>
        '''
        # Check if the config ID has been passed
        report_config_id = request.query_params.get('config')
        if not report_config_id:
            msg = 'Report config wasn\'t passed'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValueError', 'message': msg},
            )

        # Generate report using service layer
        try:
            result = self.get_object()
            report = ReportService.generate_report(result.id, report_config_id)
        except ValidationError as e:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValidationError', 'message': str(e)},
            )

        return Response(data=report)
