# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from urllib.parse import urljoin

from django.conf import settings
from rest_framework.serializers import ValidationError


def get_current_scheme_host_prefix(request):
    return (
        urljoin(f'{request.scheme}://{request.get_host()}', settings.URL_PREFIX).rstrip('/')
        + '/'
    )


def build_absolute_uri(request, endpoint):
    '''
    This is a wrapper for basic request.build_absolute_uri()
    that respects URL_PREFIX.
    '''
    return urljoin(get_current_scheme_host_prefix(request), endpoint.lstrip('/'))


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
