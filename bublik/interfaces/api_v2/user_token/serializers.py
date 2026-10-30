# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers

from bublik.data.models import UserToken


class UserTokenSerializer(serializers.Serializer):
    """A listed token. Never includes the value or its hash."""

    id = serializers.IntegerField()
    name = serializers.CharField()
    prefix = serializers.CharField()
    status = serializers.CharField()
    owner = serializers.EmailField()
    created = serializers.DateTimeField()
    expires_at = serializers.DateTimeField(allow_null=True)
    last_used_at = serializers.DateTimeField(allow_null=True)
    revoked_at = serializers.DateTimeField(allow_null=True)
    revoked_by = serializers.EmailField(allow_null=True)


class IssuedUserTokenSerializer(UserTokenSerializer):
    """A newly issued token, including its value."""

    token = serializers.CharField()

    def to_representation(self, instance):
        data = UserTokenSerializer(instance.token).data
        data['token'] = instance.value
        return data


class UserTokenCreateRequestSerializer(serializers.Serializer):
    """The request body for issuing a token."""

    name = serializers.CharField(max_length=UserToken._meta.get_field('name').max_length)
    # Required: a never-expiring token must be asked for, not fallen into.
    expires_in = serializers.IntegerField(
        allow_null=True,
        help_text='Lifetime in days: 7, 30, 90, or null for a token that never expires.',
    )
