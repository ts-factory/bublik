# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db import models

from bublik.data.models.meta import Meta
from bublik.data.models.reference import Reference
from bublik.data.models.result import TestIterationResult


__all__ = [
    'Expectation',
    'ExpectMeta',
]


class Expectation(models.Model):
    '''
    TRC test expectation instance.
    '''

    hashable = ('expectmeta_set',)

    results = models.ManyToManyField(
        TestIterationResult,
        related_name='expectations',
        help_text='Associated test iteration results.',
    )
    hash = models.CharField(
        max_length=64,
        unique=True,
        help_text='Hash of expect metas pointing to this expectation',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_expectation'

    def __repr__(self):
        return f'Expectation(results={self.results!r})'


class ExpectMeta(models.Model):
    '''
    Expectation metas.
    '''

    hashable = ('serial', 'meta', 'reference')

    meta = models.ForeignKey(Meta, on_delete=models.CASCADE, help_text='A metadata identifier.')
    reference = models.ForeignKey(
        Reference,
        on_delete=models.CASCADE,
        null=True,
        help_text='A reference identifier.',
    )
    expectation = models.ForeignKey(
        Expectation,
        on_delete=models.CASCADE,
        help_text='Expectation instance.',
    )
    serial = models.IntegerField(
        default=0,
        help_text='''\
Serial number of a meta result, can be used to determine verdicts order.''',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_expectmeta'

    def __repr__(self):
        return 'ExpectMeta(meta={}, expectation={}, serial={}, reference={})'.format(
            repr(self.meta),
            repr(self.expectation),
            repr(self.reference),
            repr(self.serial),
        )
