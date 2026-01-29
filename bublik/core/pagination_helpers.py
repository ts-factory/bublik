# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

'''Pagination utilities for non-DRF contexts (e.g., MCP tools).'''
from __future__ import annotations

from collections import OrderedDict

from django.core.exceptions import ValidationError


class PaginatedResult:
    '''Helper to paginate querysets for non-DRF contexts (e.g., MCP tools).'''

    @staticmethod
    def paginate_queryset(
        queryset,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        """Apply pagination to a queryset and return paginated result.

        Args:
            queryset: Django QuerySet or list to paginate
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dict with 'pagination' and 'results' keys

        Raises:
            ValidationError: if page/page_size are invalid
        """
        default_page = 1
        default_page_size = 25
        max_page_size = 10000

        page = page if page is not None else default_page
        page_size = page_size if page_size is not None else default_page_size

        if page < 1:
            msg = f'Page number must be >= 1, got {page}'
            raise ValidationError(msg)

        if page_size < 1:
            msg = f'Page size must be >= 1, got {page_size}'
            raise ValidationError(msg)

        if page_size > max_page_size:
            msg = f'Page size must be <= {max_page_size}, got {page_size}'
            raise ValidationError(msg)

        total_count = len(queryset)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size

        paginated_data = queryset[start_idx:end_idx]

        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0

        pagination = OrderedDict(
            [
                ('count', total_count),
                ('next', f'page={page + 1}' if page < total_pages else None),
                ('previous', f'page={page - 1}' if page > 1 else None),
            ],
        )

        return OrderedDict(
            [
                ('pagination', pagination),
                ('results', paginated_data),
            ],
        )
