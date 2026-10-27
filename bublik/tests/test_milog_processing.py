# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.core.importruns.milog import HandlerArtifacts
from bublik.data.models import (
    ChartView,
    Measurement,
    MeasurementResult,
    MeasurementResultList,
)
from bublik.tests.fake_generator import gen_test_iteration_result_simple


def entry(value, aggr='single'):
    return {'aggr': aggr, 'value': value, 'base_units': '', 'multiplier': '1'}


def result(n, entries):
    return {
        'name': f'name{n}',
        'type': f'type{n}',
        'description': f'description{n}',
        'entries': entries,
    }


class MiLogHandleTest(TestCase):
    def setUp(self):
        self.result = gen_test_iteration_result_simple('2077.01.01')
        mi_log = {
            'type': 'measurement',
            'version': '1',
            'tool': 'mytool',
            'keys': {'foo': 'bar'},
            'results': [
                result(1, [entry(1), entry(2)]),
                result(2, [entry(3), entry(4)]),
                result(3, [entry(5, aggr='max')]),
            ],
            'views': [
                {
                    'name': 'graph1',
                    'type': 'line-graph',
                    'title': 'graph1title',
                    'axis_x': {'name': 'name1', 'type': 'type1'},
                },
                {
                    'name': 'graph2',
                    'type': 'line-graph',
                    'axis_x': {'name': 'name2', 'type': 'type2'},
                    'axis_y': [{'name': 'name1', 'type': 'type1'}],
                },
                {
                    'name': 'graph3',
                    'type': 'point',
                    'value': {'name': 'name3', 'type': 'type3', 'aggr': 'max'},
                },
            ],
        }
        HandlerArtifacts(self.result).handle_mi_artifact(mi_log)

    def measurement(self, n):
        return Measurement.objects.get(metas__name='name', metas__value=f'name{n}')

    def chart_views(self, view_name, view_type):
        return list(
            ChartView.objects.filter(
                view__metas__value=view_name,
                type=view_type,
                result=self.result,
            ).values_list('measurement', flat=True),
        )

    def test_measurement_has_metas_of_all_levels(self):
        metas = set(self.measurement(1).metas.values_list('name', 'value'))
        assert metas == {
            ('type', 'measurement'),
            ('version', '1'),
            ('tool', 'mytool'),
            ('foo', 'bar'),
            ('name', 'name1'),
            ('type', 'type1'),
            ('description', 'description1'),
            ('aggr', 'single'),
            ('base_units', ''),
            ('multiplier', '1'),
        }

    def test_entries_of_the_same_measurement_make_a_list(self):
        lists = MeasurementResultList.objects.filter(result=self.result)
        assert sorted(lists.values_list('value', flat=True)) == [[1, 2], [3, 4]]
        singles = MeasurementResult.objects.filter(result=self.result)
        single = (self.measurement(3).id, 5)
        assert list(singles.values_list('measurement', 'value')) == [single]

    def test_line_graph_without_axis_y_takes_the_other_lists(self):
        assert self.chart_views('graph1', 'X') == [self.measurement(1).id]
        assert self.chart_views('graph1', 'Y') == [self.measurement(2).id]

    def test_line_graph_with_axis_y(self):
        assert self.chart_views('graph2', 'X') == [self.measurement(2).id]
        assert self.chart_views('graph2', 'Y') == [self.measurement(1).id]

    def test_point(self):
        assert self.chart_views('graph3', 'P') == [self.measurement(3).id]
