# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from bublik.core.shortcuts import build_absolute_uri, serialize
from bublik.data.serializers import EndpointURLSerializer


class URLShortnerView(APIView):
    def get(self, request, *args, **kwargs):
        '''
        Return a short URL corresponding to the passed URL.
        Short URL format is 'http://<host name>/bublik/eh/<hash>'.
        Route: /url_shortner.
        '''
        # Get URL to be shortened
        url = self.request.query_params.get('url', '')

        # Get URL endpoint
        url_head = build_absolute_uri(request, settings.UI_PREFIX)
        if url.find(url_head) != 0:
            msg = 'The passed URL has an incorrect prefix'
            return Response({'message': msg}, status=status.HTTP_400_BAD_REQUEST)
        endpoint = url.replace(url_head, '', 1)

        # Get or create EndpointURL object by endpoint
        short_url_serializer = serialize(EndpointURLSerializer, {'endpoint': endpoint})
        short_url_obj, _ = short_url_serializer.get_or_create()

        # Build short URL with the hash of the endpoint of the passed URL
        short_url_endpoint = f'eh/{short_url_obj.hash}'
        short_url = build_absolute_uri(request, short_url_endpoint)

        return Response(data={'short_url': short_url}, status=status.HTTP_200_OK)
