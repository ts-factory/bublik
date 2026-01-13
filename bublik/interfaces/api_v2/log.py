# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
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
        r'''
        Return the URLs of the corresponding JSON log file and attachments file.
        Route: /api/v2/logs/<ID>/json/?page=<page\>.
        '''
        page_str = request.query_params.get('page')
        page = int(page_str) if page_str else None
        request_origin = request.get_host() if request else None

        try:
            urls = LogService.get_json_log_urls(int(pk), page, request_origin)
            return Response(data=urls)
        except DjangoValidationError as e:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': str(e)},
            )

    @action(detail=True, methods=['get'])
    def html(self, request, pk=None):
        r'''
        Return the URL to the corresponding HTML log file.
        Route: /api/v2/logs/<ID>/html/.
        '''

        try:
            url = LogService.get_html_log_url(int(pk))
            return Response(data={'url': url})
        except DjangoValidationError as e:
            return Response(
                status=status.HTTP_404_NOT_FOUND,
                data={'message': str(e)},
            )

    @action(detail=False, methods=['get'])
    def proxy(self, request):
        r'''
        Forward the request to the given URL and return the response data.
        Route: /api/v2/logs/proxy/?url=<forwarding_url>
        '''
        forward_url = request.query_params.get('url')

        try:
            content = LogService.fetch_log_content(forward_url)
            # If there's an error key, return error response
            if isinstance(content, dict) and 'error' in content:
                return Response(
                    status=status.HTTP_502_BAD_GATEWAY,
                    data={'message': content['error']},
                )
            # Return the JSON content directly
            return Response(data=content)
        except DjangoValidationError as e:
            return Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={'message': str(e)},
            )
