# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from bublik.data.models import User


__all__ = [
    'RegisterSerializer',
    'TokenPairSerializer',
    'UserSerializer',
    'ForgotPasswordSerializer',
    'PasswordResetSerializer',
]


class RegisterSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(
        required=True,
        validators=[
            UniqueValidator(
                queryset=User.objects.all(),
                message='User with this email already exists',
            ),
        ],
    )
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
    )
    password_confirm = serializers.CharField(write_only=True, required=True)

    class Meta:
        model: typing.ClassVar = User
        fields = ('password', 'password_confirm', 'email', 'first_name', 'last_name')

    def validate(self, attrs):
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({'password': "Password fields don't match"})

        return attrs

    def create(self, validated_data):
        user = User.objects.create(
            email=validated_data['email'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            is_active=False,
        )

        user.set_password(validated_data['password'])
        user.save()

        return user


class TokenPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        token['email'] = user.email
        return token


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model: typing.ClassVar = User
        fields: typing.ClassVar['str'] = [
            'pk',
            'email',
            'password',
            'first_name',
            'last_name',
            'roles',
            'is_active',
        ]
        extra_kwargs: typing.ClassVar['dict'] = {'password': {'write_only': True}}


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        # Check if the provided email exists in the database
        try:
            User.objects.get(email=value)
        except ObjectDoesNotExist:
            msg = 'No user found with this email.'
            raise serializers.ValidationError(msg) from None

        return value


class PasswordResetSerializer(serializers.Serializer):
    current_password = serializers.CharField(
        write_only=True,
        required=False,
    )
    new_password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
    )
    new_password_confirm = serializers.CharField(write_only=True, required=True)

    def validate_passwords(self, passwords):
        # Check if new password and its confirmation were passed
        # and if the new password is valid
        self.is_valid(passwords)
        # Check if the new password fields match
        if passwords['new_password'] != passwords['new_password_confirm']:
            raise serializers.ValidationError({'new_password': "Password fields don't match"})

    def current_password_check(self, user, current_password):
        # Check if the passed current password is valid for the passed user
        if not user.check_password(current_password):
            raise AuthenticationFailed(
                {'current_password': 'Invalid password'},
            )
