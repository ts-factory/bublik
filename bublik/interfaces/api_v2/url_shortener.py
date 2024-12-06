# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import re

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from bublik.core.run.utils import prepare_date
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

        # Check URL preffix
        # Build the expected URL head based on UI_PREFIX
        url_head = f"{request.scheme}://{request.get_host()}/{settings.UI_PREFIX}"

        # Check if the passed URL starts with the expected prefix
        if not url.startswith(url_head):
            msg = 'The passed URL has an incorrect prefix'
            return Response({'message': msg}, status=status.HTTP_400_BAD_REQUEST)

        # Get view and endpoint
        url_tail = url.replace(url_head + '/', '', 1)
        view = re.split(r'/|\?', url_tail, 1)[0]
        endpoint = url_tail.replace(view, '', 1)

        # Get or create EndpointURL object
        short_url_serializer = serialize(
            EndpointURLSerializer,
            {'created': prepare_date(datetime.now()), 'view': view, 'endpoint': endpoint},
        )
        short_url_obj, _ = short_url_serializer.get_or_create()

        # Build short URL with the hash of the endpoint of the passed URL
        short_url_endpoint = f'/short/{view}/{short_url_obj.hash}'
        short_url = request.build_absolute_uri(short_url_endpoint)

        return Response(data={'short_url': short_url}, status=status.HTTP_200_OK)
