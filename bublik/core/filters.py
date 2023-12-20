# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django_filters import filters

from bublik.core.fields import ModelMultipleChoiceField


class ModelMultipleChoiceFilter(filters.ModelMultipleChoiceFilter):
    field_class = ModelMultipleChoiceField
