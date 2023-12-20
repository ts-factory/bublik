# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django_filters.filters import DateFilter, Filter
from django_filters.rest_framework import FilterSet

from bublik.core.run.fields import TestNameField
from bublik.data import models


class TestNameFilter(Filter):
    field_class = TestNameField


class TestIterationResultFilter(FilterSet):
    start_date = DateFilter(field_name='start', lookup_expr='gte')
    finish_date = DateFilter(field_name='finish', lookup_expr='lte')
    test_name = TestNameFilter(field_name='iteration__test')

    # TODO: Configure ordering

    class Meta:
        model = models.TestIterationResult
        fields = ('start_date', 'finish_date', 'test_name')
