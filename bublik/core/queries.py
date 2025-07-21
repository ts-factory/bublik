# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db.models import F, Q


def get_or_none(objects, *args, **kwargs):
    try:
        return objects.get(*args, **kwargs)
    except Exception:
        return None


class MetaResultsQuery:
    def __init__(self, meta_results):
        self.results = meta_results

    def query_to_values(self, q, order=None):
        if order is None:
            order = ['meta__category__priority', 'meta__name']
        return (
            self.results.filter(q)
            .values(name=F('meta__name'), value=F('meta__value'))
            .order_by(*order)
        )

    def labels_query(self, excluding_category_names, project_id):
        return self.query_to_values(
            Q(meta__type='label')
            & (Q(meta__category__project_id=project_id) | Q(meta__category__isnull=True))
            & ~Q(meta__category__name__in=excluding_category_names),
        )

    def special_labels_query(self, including_category_names):
        return (
            self.results.filter(
                Q(meta__type='label') & Q(meta__category__name__in=including_category_names),
            )
            .values_list(F('meta__category__name'), F('meta__value'))
            .order_by('meta__category__priority', 'meta__value')
        )

    def metas_query(self, meta_type):
        return self.query_to_values(Q(meta__type=meta_type))

    def tags_query(self):
        return self.query_to_values(
            q=Q(meta__type='tag')
            & (Q(meta__category__priority__exact=4) | Q(meta__category__isnull=True)),
        )

    def important_tags_query(self):
        return self.query_to_values(
            q=Q(meta__type='tag') & Q(meta__category__priority__range=(1, 3)),
        )
