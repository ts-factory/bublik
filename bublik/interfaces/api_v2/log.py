# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.run.external_links import get_result_log, get_sources
from bublik.data.models import TestIterationResult
from bublik.data.serializers import TestIterationResultSerializer


__all__ = [
    'LogViewSet',
]


class LogViewSet(RetrieveModelMixin, GenericViewSet):
    queryset = TestIterationResult.objects.all()
    serializer_class = TestIterationResultSerializer

    @action(detail=True, methods=['get'])
    def json(self, request, pk=None):
        r'''
        Return the URL to the corresponding JSON log file.
        Route: /api/v2/logs/<ID>/json/?page=<page\>.
        '''

        if not pk.isdigit():
            message = f'Incorrect id: {pk}. Expecting number'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': message},
            )

        result = self.get_object()
        run_source_link = get_sources(result)

        if not run_source_link:
            return Response(data={'url': None}, status=status.HTTP_200_OK)

        page = request.query_params.get('page')
        if page and not page.isdigit():
            message = f'Incorrect value for page query parameter: {page}. Expecting number'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': message},
            )

        if not result.test_run:
            # The file `node_1_0.json` contains TE startup log in JSON
            json_tail = 'json/node_1_0.json'
        else:
            json_tail = f'json/node_id{result.exec_seqno}'
            if page == '0':
                json_tail += '_all.json'
            elif page:
                json_tail += f'_p{page}.json'
            else:
                json_tail += '.json'

        url = os.path.join(run_source_link, json_tail)
        return Response(data={'url': url}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def html(self, request, pk=None):
        r'''
        Return the URL to the corresponding HTML log file.
        Route: /api/v2/logs/<ID>/html/.
        '''

        result = self.get_object()
        return Response(data={'url': get_result_log(result)}, status=status.HTTP_200_OK)
