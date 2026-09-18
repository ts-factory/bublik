# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class URLShortenerQuerySerializer(serializers.Serializer):
    url = serializers.URLField(
        help_text='Absolute Bublik UI URL to shorten, including its query parameters',
    )


class URLShortenerResponseSerializer(serializers.Serializer):
    short_url = serializers.URLField()
