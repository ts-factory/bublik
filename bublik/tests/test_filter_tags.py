# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import timedelta
import logging

from django.db import connection, reset_queries
from django.test import TestCase
from django.test.utils import override_settings
from django.utils import timezone

from bublik.core.argparse import parser_type_date
from bublik.core.run.stats import get_test_runs
from bublik.data.models import Meta, MetaResult, TestIterationResult


logger = logging.getLogger('bublik.server')


class TestMeta(object):
    def __init__(self, name, value=None, obj=None):
        self.name = str(name)
        self.value = str(value)
        self.obj = obj

    def __str__(self):
        if self.value:
            return f'{self.name}={self.value}'
        else:
            return self.name

    def __repr__(self):
        return str(self)

    def __eq__(self, other):
        return self.name == other.name and self.value == other.value


class TestTagsFiltering(TestCase):
    def __init__(self, arg):
        super().__init__(arg)

        self.metas = []
        self.results = []
        self.date = parser_type_date('2018.03.01')
        self.success = True

    def _get_meta_obj(self, meta):
        for m in self.metas:
            if m == meta:
                return m.obj
        return None

    def setUp(self):
        def create_test_run(metas=[]):
            res = TestIterationResult.objects.create(start=self.date, finish=self.date)
            res.save()
            self.results.append(res)

            for meta in metas:
                obj = self._get_meta_obj(meta)
                if obj == None:
                    meta_hash = hash(meta.name + meta.value)
                    obj = Meta.objects.create(
                        type='tag', name=meta.name, value=meta.value, hash=meta_hash
                    )
                    obj.save()
                    meta.obj = obj
                    self.metas.append(meta)

                meta_results = MetaResult(result=res, meta=obj)
                meta_results.save()
            self.date += timedelta(days=1)

        ######
        logger.info('Generate database data')
        self.date = parser_type_date('2018.03.01')
        current_tz = timezone.get_current_timezone()
        self.date = current_tz.localize(self.date)

        create_test_run([TestMeta('t1')])  # id=1
        create_test_run([TestMeta('t2')])  # id=2
        create_test_run([TestMeta('t3', 1)])  # id=3
        create_test_run([TestMeta('t1'), TestMeta('t3', 1)])  # id=4
        create_test_run([TestMeta('t4'), TestMeta('t3', 2)])  # id=5
        create_test_run([TestMeta('t5'), TestMeta('t3', 3)])  # id=6
        create_test_run([TestMeta('t6'), TestMeta('t3', 4)])  # id=7
        create_test_run([TestMeta('t1'), TestMeta('t4')])  # id=8

    @override_settings(DEBUG=True)
    def test_tags_expression(self):
        """Test runs fitlering using tags expressions."""

        def test(ex_str, expected_res):
            reset_queries()
            res = get_test_runs(finish_date=self.date, tags=ex_str, order_by='start')
            res = res.values_list('id', flat=True)

            try:
                if not any(set(res) ^ set(expected_res)):
                    verdict = 'PASS'
                else:
                    verdict = 'FAIL'
                    self.success = False
            except:
                print(connection.queries)
                raise

            logger.info(f'{verdict}: `{ex_str}`')
            details = f'Expected: {expected_res}\n'
            details += f'Obtained : {list(res)}'
            print(details)

        ######
        logger.info('Begin tags expression filtering test')

        test('t1', [1, 4, 8])
        test('t2|t1', [1, 2, 4, 8])
        test('t1|t2', [1, 2, 4, 8])
        test('t1&t3', [4])
        test('t1|t3', [1, 3, 4, 5, 6, 7, 8])
        test('t1&t3=1', [4])
        test('t1&t3=2', [])
        test('t3>1', [5, 6, 7])
        test('!t1', [2, 3, 5, 6, 7])
        test('!t1&!t2', [3, 5, 6, 7])
        test('!t1|!t2', [1, 2, 3, 4, 5, 6, 7, 8])
        test('t3&t3!=3', [3, 4, 5, 7])
        test('t3&!(t3=4|t1)', [3, 5, 6])

        if self.success:
            logger.info('End the test PASSED')
        else:
            logger.info('The test FAILED')
            exit(1)
