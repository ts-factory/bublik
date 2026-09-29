# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

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
