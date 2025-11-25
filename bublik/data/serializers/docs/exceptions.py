# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ErrorResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    message = serializers.CharField()
