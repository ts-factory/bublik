# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class DefaultPageNumberPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 10000

    def get_pagination(self):
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
