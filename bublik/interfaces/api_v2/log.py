# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.log.services import LogService
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
        '''
        Return the URLs of the corresponding JSON log file and attachments file.
        Route: /api/v2/logs/<ID>/json/?page=<page\\>.
        '''
        page_str = request.query_params.get('page')
        page = int(page_str) if page_str else None
        request_origin = request.get_host() if request else None

        # Let LogService handle validation and raise appropriate exceptions
        # The custom exception handler will format the response
        urls = LogService.get_json_log_urls(int(pk), page, request_origin)
        return Response(data=urls)

    @action(detail=True, methods=['get'])
    def html(self, request, pk=None):
        '''
        Return the URL to the corresponding HTML log file.
        Route: /api/v2/logs/<ID>/html/.
        '''

        # Let LogService handle validation and raise appropriate exceptions
        # The custom exception handler will format the response
        url = LogService.get_html_log_url(int(pk))
        return Response(data={'url': url})

    @action(detail=False, methods=['get'])
    def proxy(self, request):
        '''
        Forward the request to the given URL and return the response data.
        Route: /api/v2/logs/proxy/?url=<forwarding_url>
        '''
        forward_url = request.query_params.get('url')

        # Let LogService handle validation and raise appropriate exceptions
        # The custom exception handler will format the response
        content = LogService.fetch_log_content(forward_url)
        return Response(data=content)
