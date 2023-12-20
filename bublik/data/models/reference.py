# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db import models


__all__ = [
    'Reference',
]


class Reference(models.Model):
    '''
    Reference types.
    '''

    hashable = ('name', 'uri')

    name = models.CharField(max_length=64, help_text='The reference type name.')
    uri = models.TextField(
        max_length=128,
        help_text='''\
The reference prefix or URI, e.g. https://site.com/path/to/logs,
https://bugzilla.somebody.com/).''',
    )

    class Meta:
        db_table = 'bublik_reference'
        unique_together = (('name'), ('uri'))

    class Admin:
        pass

    def __repr__(self):
        return f'Reference(name={self.name!r}, uri={self.uri!r})'
