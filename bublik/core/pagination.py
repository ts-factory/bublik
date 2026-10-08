# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict

from django.core.paginator import EmptyPage, PageNotAnInteger
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

        try:
            self.page = paginator.page(page_number)
        except PageNotAnInteger as exc:
            raise ValidationError({'page': [str(exc)]}) from exc
        except EmptyPage:
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
