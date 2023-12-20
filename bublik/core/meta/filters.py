# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.validators import EMPTY_VALUES
from django.db.models import Q
from django.forms.fields import ChoiceField
from django_filters.filters import ChoiceFilter, NumberFilter
from django_filters.rest_framework import FilterSet

from bublik.core.filters import ModelMultipleChoiceFilter
from bublik.core.utils import choices_keys_as_values
from bublik.data import models


class TagsFilter(ChoiceFilter):
    field_class = ChoiceField

    TAGS_CHOICES = ('important', 'relevant', 'irrelevant', 'all', '')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, choices=self.__generate_choices(), **kwargs)

    def __generate_choices(self):
        return choices_keys_as_values(self.TAGS_CHOICES)

    def __apply_choice(self, value):
        tags_choice_handlers = {
            'important': Q(category__priority__range=(1, 3)),
            'relevant': Q(category__priority__exact=4) | Q(category__isnull=True),
            'irrelevant': Q(category__priority__exact=10),
            'all': Q(),
        }
        return Q(type='tag') & tags_choice_handlers.get(value)

    def meta_filter(self, qs, value):
        if value in EMPTY_VALUES:
            return qs

        qs = self.get_method(qs)(self.__apply_choice(value))
        return qs.distinct() if self.distinct else qs


class MetaFilter(FilterSet):
    result_id = NumberFilter(field_name='metaresult__result__id', label='Result ID')

    category = ModelMultipleChoiceFilter(
        field_name='category__name',
        to_field_name='name',
        queryset=models.MetaCategory.objects.filter(),
        label='Category',
    )

    tags = TagsFilter(field_name='name', label='Tags')

    class Meta:
        model = models.Meta
        fields = ('name', 'type', 'value', 'result_id', 'category', 'tags')
