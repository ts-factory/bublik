# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import PolymorphicProxySerializer
from rest_framework import serializers


class ErrorMessagesListResponseSerializer(serializers.Serializer):
    messages = serializers.ListField(child=serializers.CharField())


class ErrorMessagesByFieldResponseSerializer(serializers.Serializer):
    messages = serializers.DictField(child=serializers.ListField(child=serializers.CharField()))


ErrorResponseSerializer = PolymorphicProxySerializer(
    component_name='ErrorResponse',
    serializers=[
        ErrorMessagesListResponseSerializer,
        ErrorMessagesByFieldResponseSerializer,
    ],
    resource_type_field_name=None,
)
