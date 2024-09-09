# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from descriptors import cachedclassproperty
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from bublik.core.queries import get_or_none


__all__ = [
    'Meta',
    'MetaCategory',
    'MetaPattern',
]


class Meta(models.Model):
    '''
    Meta information to mark runs and iteration results in arbitrary way.
    '''

    hashable = ('name', 'type', 'value', 'comment')

    name = models.CharField(max_length=64, null=True, help_text='The meta name.')
    type = models.CharField(
        max_length=64,
        help_text='''\
The meta type, enumeration: result, verdict, note, error, tag, label, \
revision, branch, repo, log, import, count, objective, comment.''',
        db_index=True,
    )
    value = models.TextField(null=True, blank=True, help_text='The meta value or none.')
    hash = models.CharField(max_length=64, unique=True, help_text='Name, type and value hash')
    comment = models.TextField(help_text='A human written comment.', null=True)

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_meta'
        indexes: ClassVar[list] = [models.Index(fields=['type', 'name', 'value'])]

    def __repr__(self):
        return (
            f'Meta(name={self.name!r}, type={self.type!r}, value={self.value!r}, '
            f'hash={self.hash!r}, comment={self.comment!r})'
        )

    @cachedclassproperty
    def passed(self):
        return get_or_none(self.objects, type='result', value='PASSED')

    @cachedclassproperty
    def failed(self):
        return get_or_none(self.objects, type='result', value='FAILED')

    @cachedclassproperty
    def skipped(self):
        return get_or_none(self.objects, type='result', value='SKIPPED')

    @cachedclassproperty
    def abnormal(self):
        return Meta.objects.filter(
            type='result',
            value__in=['KILLED', 'CORED', 'FAKED', 'INCOMPLETE'],
        )


class MetaCategory(models.Model):
    '''
    Meta priority/category association model
    '''

    name = models.CharField(
        max_length=64,
        unique=True,
        null=False,
        blank=False,
        help_text='Name of the category.',
    )
    priority = models.IntegerField(
        default=4,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
        help_text='Priority assigned to the metas(tags) within the category.',
    )
    comment = models.TextField(null=True, help_text='User-defined notes about the category.')
    metas = models.ManyToManyField(
        Meta,
        related_name='category',
        help_text='Meta instances belonging to its category.',
    )
    type = models.CharField(
        max_length=64,
        help_text='''\
The meta type, enumeration: result, verdict, note, error, tag, label, \
revision, branch, repo, log, import, count, objective.''',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_metacategory'

    def __repr__(self):
        return (
            f'MetaCategory(name={self.name!r}, priority={self.priority!r}, '
            'comment={self.comment!r}, metas={self.metas!r})'
        )


class MetaPattern(models.Model):
    '''
    Meta pattern
    '''

    pattern = models.CharField(
        max_length=256,
        null=False,
        blank=False,
        help_text='Regular expression pattern that classifies a meta.',
    )
    category = models.ForeignKey(
        MetaCategory,
        related_name='pattern',
        on_delete=models.CASCADE,
        help_text='Category to which a meta resolves.',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_metapattern'

    def __repr__(self):
        return f'MetaPattern(pattern={self.pattern!r}, category={self.category!r})'
