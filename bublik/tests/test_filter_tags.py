# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from bublik.core.argparse import parser_type_date
from bublik.core.run.stats import get_test_runs
from bublik.data.models import Meta, MetaResult, Project, TestIterationResult


class TestTagsFiltering(TestCase):
    def setUp(self):
        project = Project.objects.create(name='test')
        self.date = timezone.make_aware(parser_type_date('2018.03.01'))
        self.runs = []
        runs_tags = (
            [('t1', None)],
            [('t2', None)],
            [('t3', '1')],
            [('t1', None), ('t3', '1')],
            [('t4', None), ('t3', '2')],
            [('t5', None), ('t3', '3')],
            [('t6', None), ('t3', '4')],
            [('t1', None), ('t4', None)],
        )
        for tags in runs_tags:
            run = TestIterationResult.objects.create(
                start=self.date,
                finish=self.date,
                project=project,
            )
            for name, value in tags:
                meta, _ = Meta.objects.get_or_create(
                    type='tag',
                    name=name,
                    value=value,
                    defaults={'hash': f'{name}={value}'},
                )
                MetaResult.objects.create(result=run, meta=meta)
            self.runs.append(run.id)
            self.date += timedelta(days=1)

    def test_tags_expression(self):
        """Test runs filtering using tags expressions."""
        cases = (
            ('t1', [1, 4, 8]),
            ('t2|t1', [1, 2, 4, 8]),
            ('t1|t2', [1, 2, 4, 8]),
            ('t1&t3', [4]),
            ('t1|t3', [1, 3, 4, 5, 6, 7, 8]),
            ('t1&t3=1', [4]),
            ('t1&t3=2', []),
            ('t3>1', [5, 6, 7]),
            ('!t1', [2, 3, 5, 6, 7]),
            ('!t1&!t2', [3, 5, 6, 7]),
            ('!t1|!t2', [1, 2, 3, 4, 5, 6, 7, 8]),
            ('t3&t3!=3', [3, 4, 5, 7]),
            ('t3&!(t3=4|t1)', [3, 5, 6]),
        )
        for expression, numbers in cases:
            runs = get_test_runs(finish_date=self.date, tags=expression)
            expected = {self.runs[number - 1] for number in numbers}
            assert set(runs.values_list('id', flat=True)) == expected, expression
