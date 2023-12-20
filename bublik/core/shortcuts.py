# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from urllib.parse import urljoin

from django.conf import settings
from rest_framework.serializers import ValidationError


def get_current_scheme_host_prefix(request):
    return os.path.join(f'{request.scheme}://{request.get_host()}', f'{settings.URL_PREFIX}/')


def build_absolute_uri(request, endpoint):
    '''
    This is a wrapper for basic request.build_absolute_uri()
    that respects URL_PREFIX.

    NB! The endpoint must be passed without the leading slash.
    https://stackoverflow.com/questions/10893374/python-confusions-with-urljoin
    '''

    return urljoin(get_current_scheme_host_prefix(request), endpoint)


def serialize(serializer_class, data, logger=None, **kwargs):
    serializer = serializer_class(data=data, **kwargs)
    if not serializer.is_valid():
        if logger:
            model_name = serializer_class.Meta.model._meta.object_name
            logger.error(
                f"can't serialize {model_name} instance: {dict(serializer.initial_data)}",
            )
        raise ValidationError(serializer.errors)
    return serializer
