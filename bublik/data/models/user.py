# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
'''
Contains models responsible for saving user data
'''

from django.contrib.auth.models import AbstractUser


__all__ = ['User']


class User(AbstractUser):
    class Meta:
        db_table = 'auth_user'
