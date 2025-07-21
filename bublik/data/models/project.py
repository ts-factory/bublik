# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.


from django.db import models


__all__ = [
    'Project',
]


class Project(models.Model):
    '''
    Projects.
    '''

    name = models.CharField(
        max_length=64,
        help_text='The project name.',
        unique=True,
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_project'

    def __repr__(self):
        return f'Project(name={self.name!r}'
