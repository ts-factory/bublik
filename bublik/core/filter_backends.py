# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import BaseFilterBackend
from rest_framework.filters import OrderingFilter as DRFOrderingFilter


class AllDjangoFilterBackend(DjangoFilterBackend):
    """
    A filter backend to add filtering for all fields by default
    """

    def get_filterset_class(self, view, queryset=None):
        """
        Return the django-filters `FilterSet` used to filter the queryset.
        """
        filterset_class = getattr(view, 'filterset_class', None)
        filter_fields = getattr(view, 'filter_fields', None)

        if filterset_class or filter_fields:
            return super().get_filterset_class(view, queryset)

        class AutoFilterSet(self.filterset_base):
            class Meta:
                model = queryset.model
                exclude = ''

        return AutoFilterSet


class ProjectFilterBackend(BaseFilterBackend):
    def filter_queryset(self, request, queryset, view):
        project = request.query_params.get('project')
        if project:
            return queryset.filter(project=project)
        return queryset


class StableOrderingFilter(DRFOrderingFilter):
    """
    Same as DRF's OrderingFilter, but always appends -id as a final
    ordering key - whatever field is actually ordered by (the view's
    default, or one requested via ?ordering=), rows tying on it still
    come back in a fixed order, so paging stays stable.
    """

    def filter_queryset(self, request, queryset, view):
        ordering = list(self.get_ordering(request, queryset, view) or [])
        if 'id' not in ordering and '-id' not in ordering:
            ordering.append('-id')
        return queryset.order_by(*ordering)
