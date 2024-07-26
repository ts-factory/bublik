# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from copy import deepcopy

from django.db.models import JSONField
from django_filters import filterset
from django_filters.rest_framework import FilterSet
from django_filters.rest_framework.filters import CharFilter


class ConfigFilter(FilterSet):
    FILTER_DEFAULTS = deepcopy(filterset.FILTER_FOR_DBFIELD_DEFAULTS)
    FILTER_DEFAULTS.update(
        {
            JSONField: {
                'filter_class': CharFilter,
                'extra': lambda f: {'lookup_expr': ['icontains']},
            },
        },
    )
