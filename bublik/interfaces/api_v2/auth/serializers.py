# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from bublik.data.serializers import UserSerializer


class AuthMessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()


class LoginResponseSerializer(serializers.Serializer):
    user = UserSerializer()


class AdminUpdateUserRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    password = serializers.CharField(write_only=True, required=False)


@extend_schema_serializer(many=False)
class CapabilitiesResponseSerializer(serializers.Serializer):
    default = serializers.ListField(
        child=serializers.CharField(),
        help_text='Actions allowed by the global settings',
    )
    projects = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        help_text='Actions allowed by the settings of each project, by project id',
    )
