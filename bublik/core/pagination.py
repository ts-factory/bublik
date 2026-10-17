# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict

from django.core.paginator import EmptyPage, PageNotAnInteger
from django.db.models import Count, F, Max, Window
from django.db.models.functions import RowNumber
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class DefaultPageNumberPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 10000

    def paginate_queryset(self, queryset, request, view=None):
        self.request = request
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        paginator = self.django_paginator_class(queryset, page_size)
        page_number = self.get_page_number(request, paginator)

        # The causes are dropped (`from None`): these are plain 400s, which
        # the exception handler would otherwise log as errors.
        try:
            self.page = paginator.page(page_number)
        except PageNotAnInteger as exc:
            raise ValidationError({'page': [str(exc)]}) from None
        except EmptyPage as exc:
            # A number below 1 is as invalid as one that is not a number; only
            # a page past the last one is an empty page.
            if int(page_number) < 1:
                raise ValidationError({'page': [str(exc)]}) from None
            self.page = None
            self._count = paginator.count
            return []

        return list(self.page)

    def get_pagination(self):
        if self.page is None:
            return OrderedDict([('count', self._count), ('next', None), ('previous', None)])
        return OrderedDict(
            [
                ('count', self.page.paginator.count),
                ('next', self.get_next_link()),
                ('previous', self.get_previous_link()),
            ],
        )

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ('pagination', self.get_pagination()),
                    ('results', data),
                ],
            ),
        )

    def get_paginated_response_schema(self, schema):
        return {
            'type': 'object',
            'required': ['pagination', 'results'],
            'properties': {
                'pagination': {
                    'type': 'object',
                    'required': ['count', 'next', 'previous'],
                    'properties': {
                        'count': {'type': 'integer', 'example': 123},
                        'next': {'type': 'string', 'nullable': True, 'example': None},
                        'previous': {'type': 'string', 'nullable': True, 'example': None},
                    },
                },
                'results': schema,
            },
        }


class PerProjectPageNumberPagination(DefaultPageNumberPagination):
    """
    A page holds up to `page_size` rows of each project, in the order of the
    list: page N is the Nth `page_size` rows of every project, so that a list
    shown grouped by project is paged in every group at once. Narrowed to one
    project, it pages as the default pagination does. `count` is the number
    of all the rows, and there is a next page while any project has rows left.
    """

    # The lookup of the project of a row.
    project_field = 'project_id'

    def paginate_queryset(self, queryset, request, view=None):
        page_size = self.get_page_size(request)
        if not page_size:
            return None

        largest = (
            queryset.order_by()
            .values(self.project_field)
            .annotate(rows=Count('pk'))
            .aggregate(largest=Max('rows'))['largest']
        )
        # Paged as the rows of the largest project would be: these decide
        # which page numbers are valid and whether there is a next page.
        rows_of_largest = super().paginate_queryset(range(largest or 0), request, view)
        self._count = queryset.count()
        if self.page is None:
            return rows_of_largest

        start = (self.page.number - 1) * page_size
        page_ids = (
            queryset.alias(
                _row_in_project=Window(
                    RowNumber(),
                    partition_by=F(self.project_field),
                    order_by=self._ordering(queryset),
                ),
            )
            .filter(_row_in_project__gt=start, _row_in_project__lte=start + page_size)
            .values('pk')
        )
        # Fetched by ID, so that what the rows carry is computed for the page
        # alone rather than for every row the window numbers.
        return list(queryset.filter(pk__in=page_ids))

    @staticmethod
    def _ordering(queryset):
        """The ordering of the queryset, as expressions for a window."""
        ordering = []
        for field in queryset.query.order_by or ('pk',):
            if hasattr(field, 'resolve_expression'):
                ordering.append(field)
            elif field.startswith('-'):
                ordering.append(F(field[1:]).desc())
            else:
                ordering.append(F(field).asc())
        return ordering

    def get_pagination(self):
        pagination = super().get_pagination()
        pagination['count'] = self._count
        return pagination
