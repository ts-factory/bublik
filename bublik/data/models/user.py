# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
"""
Contains models responsible for saving user data
"""

import typing

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, models

from bublik.core.exceptions import UserStatusError


__all__ = ['User', 'UserManager', 'UserRoles', 'UserStatus']


class UserManager(BaseUserManager):
    """
    Custom user model manager where email is the unique identifiers
    for authentication instead of usernames.
    """

    def create_user(self, email, password, **extra_fields):
        """
        Create and save a user with the given email and password.
        """
        if not email:
            msg = 'The email must be set'
            raise ValueError(msg)
        extra_fields.setdefault('status', UserStatus.ACTIVE)
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        """
        Create and save an admin user with the given email and password.
        """
        extra_fields.setdefault('status', UserStatus.ACTIVE)
        extra_fields.setdefault('roles', UserRoles.ADMIN)

        if extra_fields.get('status') != UserStatus.ACTIVE:
            msg = 'Admin must have status=active'
            raise ValueError(msg)
        if extra_fields.get('roles') is not UserRoles.ADMIN:
            msg = 'Admin must have roles=admin'
            raise ValueError(msg)
        return self.create_user(email, password, **extra_fields)

    def create_system_user(self):
        """
        Create and save the system user.
        """
        system_user = self.model(is_system=True, status=UserStatus.ACTIVE)
        system_user.save()
        return system_user


class UserRoles(models.TextChoices):
    ADMIN = 'admin'
    USER = 'user'


class UserStatus(models.TextChoices):
    PENDING = 'pending'
    ACTIVE = 'active'
    DEACTIVATED = 'deactivated'


class User(AbstractUser):
    username = None
    last_login = None
    is_superuser = None
    is_staff = None
    email = models.EmailField('Email address', unique=True)
    roles = models.CharField(
        'User roles',
        choices=UserRoles.choices,
        max_length=64,
        default=UserRoles.USER,
    )
    first_name = models.CharField('First name', max_length=64)
    last_name = models.CharField('Last name', max_length=64)
    is_system = models.BooleanField(default=False)
    status = models.CharField(
        'User status',
        choices=UserStatus.choices,
        max_length=16,
        default=UserStatus.PENDING,
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS: typing.ClassVar[list] = []

    objects = UserManager()

    @property
    def is_active(self):
        return self.status == UserStatus.ACTIVE

    def activate(self):
        """
        Activate the user waiting for email verification.
        """
        if self.status != UserStatus.PENDING:
            msg = f'Only a pending user can be activated, the user is {self.status}'
            raise UserStatusError(msg)
        self.status = UserStatus.ACTIVE
        self.save()

    def deactivate(self):
        """
        Deactivate the user and end all their sessions.
        """
        from bublik.core.auth import revoke_refresh_tokens  # noqa: PLC0415

        self.status = UserStatus.DEACTIVATED
        self.save()
        revoke_refresh_tokens(self)

    def __repr__(self):
        return (
            f'User(id={self.pk!r}, '
            f'email={self.email!r}, '
            f'roles={self.roles!r}, '
            f'first_name={self.first_name!r}, '
            f'last_name={self.last_name!r})'
        )

    def save(self, *args, **kwargs):
        if self.is_system and User.objects.filter(is_system=True).exclude(id=self.id).exists():
            msg = 'The system user already exists'
            raise IntegrityError(msg)

        super().save(*args, **kwargs)

    @staticmethod
    def get_or_create_system_user():
        try:
            return User.objects.get(is_system=True)
        except ObjectDoesNotExist:
            return User.objects.create_system_user()

    class Meta:
        db_table = 'bublik_user'
