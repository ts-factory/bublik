# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models

from bublik.core.utils import get_metric_prefix_units, key_value_transforming
from bublik.data.models.meta import Meta
from bublik.data.models.result import TestIterationResult


__all__ = [
    'Measurement',
    'MeasurementResult',
    'View',
    'ChartViewType',
    'ChartView',
]


class ChartViewType:
    '''
    This class stands for converting a type of measurement reference
    to the one symbol.
    '''

    AXIS_X = 'axis_x'
    AXIS_Y = 'axis_y'
    POINT = 'point'

    SET: ClassVar[dict] = {AXIS_X: 'X', AXIS_Y: 'Y', POINT: 'P'}

    INV_SET: ClassVar[dict] = {v: k for k, v in SET.items()}

    @classmethod
    def conv(cls, item):
        return cls.SET.get(item)

    @classmethod
    def default(cls):
        return cls.SET.get(cls.POINT)

    @classmethod
    def choices(cls):
        return tuple(cls.INV_SET.items())


class Measurement(models.Model):
    '''
    The table to describe measurement characteristics.
    '''

    hashable = ('metas',)

    metas = models.ManyToManyField(
        Meta,
        related_name='measurements',
        help_text='A metadata identifier.',
    )
    hash = models.CharField(max_length=64, help_text='Hash by hash_salt and all Meta fields')

    class Meta:
        db_table = 'bublik_measurement'

    def get_multiplier(self):
        meta = self.metas.get(name='multiplier')
        if meta:
            return meta.value
        return None

    def representation(self):
        data = {
            'measurement_id': self.id,
            'type': None,
            'name': None,
            'tool': None,
            'aggr': None,
            'units': None,
            'keys': [],
            'comments': [],
        }

        for m in self.metas.all():
            if m.name == 'type' and m.type == 'measurement_subject':
                data['type'] = m.value
            if m.name == 'name' and m.type == 'measurement_subject':
                data['name'] = m.value
            if m.name == 'tool':
                data['tool'] = m.value
            if m.name == 'aggr':
                data['aggr'] = m.value
            if m.name == 'base_units':
                data['units'] = m.value
            if m.type == 'measurement_key':
                data['keys'].append(key_value_transforming(m.name, m.value))
            if m.type == 'measurement_comment':
                data['comments'].append(key_value_transforming(m.name, m.value))

        # apply multiplier for base units
        if data['units']:
            multiplier = self.get_multiplier()
            if multiplier:
                data['units'] = get_metric_prefix_units(multiplier, data['units'])

        return data


class MeasurementResult(models.Model):
    '''
    The table to combine measurement results and its measurement characteristics.
    '''

    measurement = models.ForeignKey(
        Measurement,
        on_delete=models.CASCADE,
        related_name='measurement_results',
        help_text='The measurement characteristics.',
    )
    value = models.FloatField(help_text='The measurement value of the result')
    result = models.ForeignKey(
        TestIterationResult,
        on_delete=models.CASCADE,
        related_name='measurement_results',
        help_text='The test iteration result which is measured.',
    )
    serial = models.IntegerField(
        default=0,
        help_text='''\
Serial number can be used to determine results order.''',
    )

    class Meta:
        db_table = 'bublik_measurementresult'

    def representation(self):
        return {
            'start': self.result.start,
            'sequence_number': self.serial,
            'value': self.value,
            'run_id': self.result.test_run.id,
            'result_id': self.result.id,
            'iteration_id': self.result.iteration.id,
        }


class MeasurementResultList(models.Model):
    '''
    The table to store sequences of measurement results.
    '''

    measurement = models.ForeignKey(
        Measurement,
        on_delete=models.CASCADE,
        help_text='The measurement characteristics.',
    )
    value = ArrayField(models.FloatField(), help_text='The measurement values of the result')
    result = models.ForeignKey(
        TestIterationResult,
        on_delete=models.CASCADE,
        help_text='The test iteration result which is measured.',
    )
    serial = models.IntegerField(
        default=0,
        help_text='Serial number can be used to determine results order',
    )

    class Meta:
        db_table = 'bublik_measurementresultlist'

    def __len__(self):
        return len(self.value)


class View(models.Model):
    '''
    The table to describe measurement view characteristics.
    '''

    hashable = ('metas',)

    metas = models.ManyToManyField(
        Meta,
        related_name='views',
        help_text='A metadata identifier',
    )
    hash = models.CharField(max_length=64, help_text='Hash by hash_salt and all Meta fields')

    class Meta:
        db_table = 'bublik_view'

    def representation(self):
        data = {
            'name': None,
            'type': None,
            'title': None,
        }
        for m in self.metas.filter(type='measurement_view'):
            if m.name == 'name':
                data['name'] = m.value
            if m.name == 'type':
                data['type'] = m.value
            if m.name == 'title':
                data['title'] = m.value
        return data


class ChartView(models.Model):
    '''
    The table to describe views of two measurements on a single graph
    '''

    view = models.ForeignKey(
        View,
        related_name='chart_views',
        on_delete=models.CASCADE,
        help_text='The view characteristics identifier',
    )
    result = models.ForeignKey(
        TestIterationResult,
        on_delete=models.CASCADE,
        related_name='chart_views',
        help_text='The test iteration result which is viewed.',
    )
    measurement = models.ForeignKey(
        Measurement,
        on_delete=models.CASCADE,
        related_name='chart_views',
        null=True,  # This is used to set the x-axis sequence from 0 to N,
        # where N is the number of measurements on the Y-axis
    )
    type = models.CharField(
        max_length=1,
        choices=ChartViewType.choices(),
        default=ChartViewType.default(),
    )

    class Meta:
        db_table = 'bublik_chartview'
