# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.core.importruns.milog import HandlerArtifacts
from bublik.data.models import (
    ChartView,
    Measurement,
    MeasurementResult,
    Meta,
    TestIterationResult,
)
from bublik.tests.fake_generator import gen_test_iteration_result_simple


class Checker:
    def __init__(self, common, result, entries, mr):
        self.common = common
        self.result = result
        self.entries = entries
        self.qs = list(mr.measurement.metas.all().values('name', 'value'))
        self.count = 0

    def compare_and_increase_count(self, k, v):
        for item in self.qs:
            if k == item['name'] and v == item['value']:
                self.count += 1

    def get_common_len(self):
        c_len = 0
        for k, v in self.common.items():
            if isinstance(v, dict):
                c_len += len(v.keys())
            else:
                c_len += 1

        return c_len

    def get_result_len(self):
        return len(self.result.keys())

    def get_entries_len(self):
        return len(self.entries.keys())

    def check_common(self):
        for k, v in self.common.items():
            if isinstance(v, dict):
                for i, j in v.items():
                    self.compare_and_increase_count(i, j)
            else:
                self.compare_and_increase_count(k, v)

    def check_result(self):
        for k, v in self.result.items():
            self.compare_and_increase_count(k, v)

    def check_entries(self):
        for k, v in self.entries.items():
            self.compare_and_increase_count(k, v)


class MiLogHandleTest(TestCase):
    def setUp(self):
        self.common = {
            'type': 'measurement',
            'version': '1',
            'tool': 'mytool',
            'keys': {'foo': 'bar'},
        }

        self.results1 = {
            'name': 'name1',
            'type': 'type1',
            'description': 'description1',
        }

        self.entries11 = {
            'aggr': 'single',
            'value': 1,
            'base_units': '',
            'multiplier': '1',
        }

        self.entries12 = {
            'aggr': 'single',
            'value': 2,
            'base_units': '',
            'multiplier': '1',
        }

        self.results2 = {
            'name': 'name2',
            'type': 'type2',
            'description': 'description2',
        }

        self.entries21 = {
            'aggr': 'single',
            'value': 3,
            'base_units': '',
            'multiplier': '1',
        }

        self.entries22 = {
            'aggr': 'single',
            'value': 4,
            'base_units': '',
            'multiplier': '1',
        }

        self.results3 = {
            'name': 'name3',
            'type': 'type3',
            'description': 'description3',
        }

        self.entries31 = {
            'aggr': 'max',
            'value': 5,
            'base_units': '',
            'multiplier': '1',
        }

        self.views1 = {
            'name': 'graph1',
            'type': 'line-graph',
            'title': 'graph1title',
            'axis_x': {
                'name': 'name1',
                'type': 'type1',
            },
        }

        self.views2 = {
            'name': 'graph2',
            'type': 'line-graph',
            'axis_x': {
                'name': 'name2',
                'type': 'type2',
            },
            'axis_y': [
                {
                    'name': 'name1',
                    'type': 'type1',
                }
            ],
        }

        self.views3 = {
            'name': 'graph3',
            'type': 'line-graph',
            'axis_x': {'name': 'auto-seqno'},
        }

        self.views4 = {
            'name': 'graph4',
            'type': 'point',
            'value': {
                'name': 'name3',
                'type': 'type3',
                'aggr': 'max',
            },
        }

        self.mi_log = {
            **self.common,
            'results': [
                {**self.results1, 'entries': [self.entries11, self.entries12]},
                {**self.results2, 'entries': [self.entries21, self.entries22]},
                {**self.results3, 'entries': [self.entries31]},
            ],
            'views': [self.views1, self.views2, self.views3, self.views4],
        }

        res = gen_test_iteration_result_simple('2077.01.01')
        HandlerArtifacts(res).handle_mi_artifact(self.mi_log.copy())

    def test_mi_log_handle(self):
        def check_measurement_result(result, entries, mr, value):
            ck = Checker(self.common, result, entries, mr)
            ck.check_common()
            ck.check_result()
            ck.check_entries()
            self.assertEqual(
                ck.count,
                ck.get_common_len() + ck.get_result_len() + ck.get_entries_len(),
                f'Not all fields were processed for the value {value}',
            )

        mr1 = MeasurementResult.objects.get(value=1)
        check_measurement_result(self.results1, self.entries11, mr1, 1)
        mr2 = MeasurementResult.objects.get(value=2)
        check_measurement_result(self.results1, self.entries12, mr2, 2)
        mr3 = MeasurementResult.objects.get(value=3)
        check_measurement_result(self.results2, self.entries21, mr3, 3)
        mr4 = MeasurementResult.objects.get(value=4)
        check_measurement_result(self.results2, self.entries22, mr4, 4)
        mr5 = MeasurementResult.objects.get(value=5)
        check_measurement_result(self.results3, self.entries31, mr5, 5)

        def check_number_created_objects(view_name, number):
            cv = ChartView.objects.filter(view__metas__value=view_name)
            self.assertEqual(
                cv.count(),
                number,
                'Invalid number of objects created for the view ' f"named {view_name}'",
            )

        check_number_created_objects('graph1', 3)
        check_number_created_objects('graph2', 2)
        check_number_created_objects('graph3', 4)
        check_number_created_objects('graph4', 1)

        def check_measurement_in_chart_view_x_or_y(view_name, measurement_x, measurements_y):
            cv = ChartView.objects.filter(view__metas__value=view_name)
            cvx = cv.filter(type='X')
            self.assertEqual(
                cvx.count(),
                1,
                'More than one ChartView with ' f"type 'X' was created for {view_name}",
            )
            if measurement_x:
                self.assertEqual(
                    cvx.first().measurement,
                    Measurement.objects.get(id=measurement_x),
                    f'ChartView has invalid measurement axis_x for {view_name}',
                )
            else:
                self.assertEqual(
                    cvx.first().measurement,
                    None,
                    f'ChartView has invalid measurement axis_x for {view_name}',
                )

            cvy = cv.filter(type='Y')
            sorted(measurements_y)
            for i, my in enumerate(measurements_y):
                self.assertEqual(
                    cvy[i].measurement,
                    Measurement.objects.get(id=my),
                    f'ChartView has invalid measurement axis_y for {view_name}',
                )

        check_measurement_in_chart_view_x_or_y('graph1', 1, [2, 3])
        check_measurement_in_chart_view_x_or_y('graph2', 2, [1])
        check_measurement_in_chart_view_x_or_y('graph3', None, [1, 2, 3])

        def check_measurement_in_chart_view_point(view_name, measurement):
            cv = ChartView.objects.filter(view__metas__value=view_name)
            self.assertEqual(
                cv.first().measurement,
                Measurement.objects.get(id=measurement),
                f'ChartView has invalid measurement point for {view_name}',
            )

        check_measurement_in_chart_view_point('graph4', 3)
