# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict, defaultdict

from django.core.cache import caches

from bublik.core.config.services import ConfigServices
from bublik.core.meta.categorization import (
    get_metas_by_category,
    group_by_runs,
    group_by_runs_and_category,
)
from bublik.core.utils import key_value_list_transforming
from bublik.data.models import GlobalConfigNames, Meta, MetaResult, TestArgument


def get_metadata_by_runs(runs, categorize=False):
    """
    Prepare metadata as following: {'run_id': ['meta_str', ], }.
    Runs items can represent TestIterationResult objects or just IDs.
    """

    metadata_results = MetaResult.objects.filter(result__in=runs).values_list(
        'result__id',
        'meta__id',
    )

    metadata_categories = ConfigServices.getattr_from_global(
        GlobalConfigNames.PER_CONF,
        'METADATA_ON_PAGES',
        default=[],
    )

    groupping_kwargs = {
        'meta_results': metadata_results,
        'categories': metadata_categories,
        'groupby_fn': group_by_runs,
        'format_fn': key_value_list_transforming,
    }

    if categorize:
        groupping_kwargs.update({'groupby_fn': group_by_runs_and_category})
    return get_metas_by_category(**groupping_kwargs)


def get_tags_by_runs(runs, not_categorize=False):
    """
    Prepare tags as following: {'run_id': ['tag_id', ], }.
    Runs items can represent TestIterationResult objects or just IDs.
    """

    if not_categorize:
        all_tags = caches['run'].get('tags', [])
    else:
        all_important_tags = caches['run'].get('important_tags', {})
        all_relevant_tags = caches['run'].get('relevant_tags', {})
        all_tags = set(all_important_tags) | set(all_relevant_tags)

    tags_results_query = MetaResult.objects.filter(
        result__in=runs,
        meta__in=all_tags,
    ).values_list('meta__id', 'result__id')

    tags_results_dict = defaultdict(list)
    for meta_id, result_id in tags_results_query:
        tags_results_dict[meta_id].append(result_id)

    def match_tags(cached_tags):
        tags = defaultdict(list)
        for meta_id, meta_value in cached_tags.items():
            if meta_id in tags_results_dict:
                for result_id in tags_results_dict[meta_id]:
                    tags[result_id].append(meta_value)
        return tags

    if not_categorize:
        return match_tags(all_tags)

    important_tags = match_tags(all_important_tags)
    relevant_tags = match_tags(all_relevant_tags)

    return important_tags, relevant_tags


def get_parameters_by_iterations(iterations):
    parameters_data = list(
        TestArgument.objects.filter(test_iterations__in=iterations)
        .values_list('test_iterations__id', 'name', 'value')
        .order_by('test_iterations__id', 'name'),
    )

    parameters = defaultdict(OrderedDict)
    for test_iteration_id, name, value in parameters_data:
        parameters[test_iteration_id][name] = value

    return parameters


def get_results(results):
    results_data = (
        MetaResult.objects.filter(result__in=results, meta__type='result')
        .values_list('result__id', 'meta__value')
        .order_by('serial')
    )

    results = {}
    for result_id, result in results_data:
        results[result_id] = result

    return results


def get_verdicts(results):
    verdicts_data = (
        MetaResult.objects.filter(result__in=results, meta__type='verdict')
        .values_list('result__id', 'meta__value')
        .order_by('serial')
    )

    verdicts = defaultdict(list)
    for result_id, verdict in verdicts_data.all():
        verdicts[result_id].append(verdict)

    return verdicts


def is_result_unexpected(result):
    return result.meta_results.filter(meta__in=Meta.objects.filter(type='err')).exists()
