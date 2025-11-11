# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.validators import UniqueValidator
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from bublik.data.models import User


__all__ = [
    'PasswordResetSerializer',
    'RegisterSerializer',
    'TokenPairSerializer',
    'UpdateUserSerializer',
    'UserEmailSerializer',
    'UserSerializer',
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
            raise serializers.ValidationError({'password': 'Password fields don\'t match'})

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


class UserEmailSerializer(serializers.Serializer):
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
        self.is_valid(raise_exception=True)
        # Check if the new password fields match
        if passwords['new_password'] != passwords['new_password_confirm']:
            raise serializers.ValidationError({'new_password': 'Password fields don\'t match'})

    def current_password_check(self, user, current_password):
        # Check if the passed current password is valid for the passed user
        if not user.check_password(current_password):
            raise PermissionDenied(
                {'current_password': 'Invalid password'},
            )


class UpdateUserSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False)
    first_name = serializers.CharField(required=False)
    last_name = serializers.CharField(required=False)
    password = serializers.CharField(
        write_only=True,
        required=False,
        validators=[validate_password],
    )

    def update(self, user, data):
        first_name = data.get('first_name', None)
        last_name = data.get('last_name', None)

        # Update the fields according to the passed data
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name

        # Update the password if provided
        password = data.get('password', None)
        if password:
            user.set_password(password)

        user.save()
        return user
