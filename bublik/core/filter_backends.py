# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django_filters.rest_framework import DjangoFilterBackend


class AllDjangoFilterBackend(DjangoFilterBackend):
    '''
    A filter backend to add filtering for all fields by default
    '''

    def get_filterset_class(self, view, queryset=None):
        '''
        Return the django-filters `FilterSet` used to filter the queryset.
        '''
        filterset_class = getattr(view, 'filterset_class', None)
        filter_fields = getattr(view, 'filter_fields', None)

        if filterset_class or filter_fields:
            return super().get_filterset_class(view, queryset)

        class AutoFilterSet(self.filterset_base):
            class Meta:
                model = queryset.model
                exclude = ''

        return AutoFilterSet
