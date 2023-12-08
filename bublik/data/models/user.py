# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
'''
Contains models responsible for saving user data
'''
import typing

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models


__all__ = ['User', 'UserManager', 'UserRoles']


class UserManager(BaseUserManager):
    '''
    Custom user model manager where email is the unique identifiers
    for authentication instead of usernames.
    '''

    def create_user(self, email, password, **extra_fields):
        '''
        Create and save a user with the given email and password.
        '''
        if not email:
            msg = 'The email must be set'
            raise ValueError(msg)
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, email, password, **extra_fields):
        '''
        Create and save an admin user with the given email and password.
        '''
        extra_fields.setdefault('is_active', True)
        extra_fields.setdefault('roles', UserRoles.ADMIN)

        if extra_fields.get('is_active') is not True:
            msg = 'Admin must have is_active=True'
            raise ValueError(msg)
        if extra_fields.get('roles') is not UserRoles.ADMIN:
            msg = 'Admin must have roles=admin'
            raise ValueError(msg)
        return self.create_user(email, password, **extra_fields)


class UserRoles(models.TextChoices):
    ADMIN = 'admin'
    USER = 'user'


class User(AbstractUser):
    username = None
    last_login = None
    is_superuser = None
    is_staff = None
    date_joined = None
    email = models.EmailField('Email address', unique=True)
    roles = models.CharField(
        'User roles',
        choices=UserRoles.choices,
        max_length=64,
        default=UserRoles.USER,
    )
    first_name = models.CharField('First name', max_length=64)
    last_name = models.CharField('Last name', max_length=64)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS: typing.ClassVar[list] = []

    objects = UserManager()

    def __repr__(self):
        return (
            f'User(id={self.pk!r}, '
            f'email={self.email!r}, '
            f'roles={self.roles!r}, '
            f'first_name={self.first_name!r}, '
            f'last_name={self.last_name!r})'
        )

    class Meta:
        db_table = 'bublik_user'
