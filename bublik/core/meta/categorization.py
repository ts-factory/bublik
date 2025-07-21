# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict
import re

from django.db.models import F

from bublik.core.utils import dicts_groupby, key_value_list_transforming
from bublik.data.models.meta import MetaPattern


def categorize_meta(meta):
    if meta.name is None:
        return

    for metapattern in MetaPattern.objects.filter(category__type=meta.type):
        if re.search(metapattern.pattern, meta.name):
            metapattern.category.metas.add(meta)


def skip_meta_name(category, metas):
    # Hide meta names if every meta name matches its category name or
    # there is only one meta of a certain category.
    if all(category == meta['name'] for meta in metas) or len(metas) == 1:
        for meta in metas:
            meta.pop('name')
            meta.pop('result_id')
    else:
        for meta in metas:
            meta.pop('result_id')
    return metas


def group_by_runs(metas, format_fn=key_value_list_transforming):
    metas_by_category = {}
    for result_id, metas_and_categories in dicts_groupby(metas, dict_key='result_id'):
        metas = list(format_fn(metas_and_categories)) if format_fn else metas_and_categories
        metas_by_category[result_id] = metas
    return metas_by_category


def group_by_category(metas, categories, format_fn=skip_meta_name):
    metas_by_category = OrderedDict.fromkeys(categories, [])
    for category, categorised_metas in dicts_groupby(metas, dict_key='category'):
        if format_fn:
            format_fn(category, categorised_metas)
        metas_by_category[category] = categorised_metas
    return metas_by_category


def group_by_runs_and_category(metas, categories, format_fn=key_value_list_transforming):
    # Group by runs
    metas_by_category = {}
    for result_id, metas_and_categories in dicts_groupby(metas, dict_key='result_id'):
        metas_by_category[result_id] = OrderedDict.fromkeys(categories, [])
        # Group by category
        for category, metas in dicts_groupby(metas_and_categories, dict_key='category'):
            metas_group = list(format_fn(metas)) if format_fn else metas
            metas_by_category[result_id][category] = metas_group
    return metas_by_category


def get_metas_by_category(
    meta_results,
    categories,
    project_id,
    groupby_fn=group_by_category,
    format_fn=skip_meta_name,
):
    if not categories:
        return {}

    # Get metas of the specified categories
    metas = list(
        meta_results.filter(
            meta__category__name__in=categories,
            meta__category__project_id=project_id,
        )
        .annotate(
            category=F('meta__category__name'),
            name=F('meta__name'),
            value=F('meta__value'),
        )
        .values('result_id', 'category', 'name', 'value')
        .order_by('category', 'name'),
    )

    # Group by via the specified function
    if groupby_fn:
        if groupby_fn == group_by_runs:
            return groupby_fn(metas, format_fn)
        return groupby_fn(metas, categories, format_fn)

    return metas
