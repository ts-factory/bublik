# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path
from urllib.parse import urlparse

from django.conf import settings
from django.http import HttpResponse
import requests
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.exceptions import BadGatewayError
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
        Return the URLs of the corresponding JSON log file and attachments file.
        Route: /api/v2/logs/<ID>/json/?page=<page\>.
        '''

        if not pk.isdigit():
            msg = f'Incorrect id: {pk}. Expecting number'
            raise ValidationError(msg)

        result = self.get_object()
        run_source_link = get_sources(result)

        if not run_source_link:
            return Response(data={'url': None})

        page = request.query_params.get('page')
        if page and not page.isdigit():
            msg = f'Incorrect value for page query parameter: {page}. Expecting number'
            raise ValidationError(msg)

        if not result.test_run:
            # The file `node_1_0.json` contains TE startup log in JSON
            node = 'node_1_0'
        else:
            node = f'node_id{result.exec_seqno}'
            if page == '0':
                node += '_all'
            elif page:
                node += f'_p{page}'

        url = os.path.join(run_source_link, 'json', node + '.json')
        attachments_url = os.path.join(run_source_link, 'attachments', node, 'attachments.json')

        if settings.ENABLE_JSON_LOGS_PROXY:
            parsed_url = urlparse(request.build_absolute_uri())
            origin = f"{'https' if settings.SECURE_HTTP else 'http'}://{parsed_url.netloc}"
            forwarding_url = f'{origin}{settings.PREFIX}/api/v2/logs/proxy/?url={url}'
            attachments_url = (
                f'{origin}{settings.PREFIX}/api/v2/logs/proxy/?url={attachments_url}'
            )

            return Response(
                data={'url': forwarding_url, 'attachments_url': attachments_url},
            )

        return Response(
            data={'url': url, 'attachments_url': attachments_url},
        )

    @action(detail=True, methods=['get'])
    def html(self, request, pk=None):
        r'''
        Return the URL to the corresponding HTML log file.
        Route: /api/v2/logs/<ID>/html/.
        '''

        result = self.get_object()
        return Response(data={'url': get_result_log(result)})

    @action(detail=False, methods=['get'])
    def proxy(self, request):
        r'''
        Forward the request to the given URL and return the response data.
        Route: /api/v2/logs/proxy/?url=<forwarding_url>
        '''
        forward_url = request.query_params.get('url')
        if not forward_url:
            msg = 'URL parameter is missing.'
            raise ValidationError(msg)

        try:
            response = requests.get(forward_url)
            response.raise_for_status()
        except requests.exceptions.RequestException as re:
            msg = 'Error forwarding request'
            raise BadGatewayError(msg) from re

        return HttpResponse(
            content=response.content,
            status=response.status_code,
            content_type=response.headers.get('Content-Type', 'application/octet-stream'),
        )
