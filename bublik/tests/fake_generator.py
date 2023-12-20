# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
"""
This module provides shortcuts to generate common in testing area objects.
It can be improved by implementing Factory.

Links to rely on:
https://github.com/joke2k/faker
https://factoryboy.readthedocs.io
"""

from bublik.core.argparse import parser_type_date
from bublik.data.models import TestIterationResult


def gen_meta_data(v=0, set_data={}):
    mock_data = f't{v}'
    data = dict.fromkeys(('name', 'type', 'value'), mock_data)
    data.update(set_data)
    return data


def gen_reference_data(v=0, set_data={}):
    data = dict(name=f't{v}1', uri=f't{v}2')
    data.update(set_data)
    return data


def gen_expect_meta_data(v=0, set_data={}):
    data = dict(meta=gen_meta_data(v), reference=gen_reference_data(v), serial=v)
    data.update(set_data)
    return data


def gen_expectation_data(n=2):
    return dict(expectmeta_set=[gen_expect_meta_data(v) for v in range(n)])


def gen_test_iteration_result_simple(date):
    date = parser_type_date(date)
    return TestIterationResult.objects.create(start=date, finish=date)
