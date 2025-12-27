# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict, defaultdict

from bublik.core.cache import ProjectCache
from bublik.core.config.services import ConfigServices
from bublik.core.meta.categorization import (
    get_metas_by_category,
    group_by_runs,
    group_by_runs_and_category,
)
from bublik.core.utils import key_value_list_transforming
from bublik.data.models import (
    GlobalConfigs,
    Meta,
    MetaResult,
    Project,
    TestArgument,
    TestIterationResult,
)


def get_metadata_by_runs(runs, categorize=False):
    """
    Prepare metadata as following: {'run_id': ['meta_str', ], }.
    Runs items can represent TestIterationResult objects or just IDs.
    """

    metadata_by_runs = {}
    project_ids = list(Project.objects.values_list('id', flat=True))
    for project_id in project_ids:
        metadata_results = MetaResult.objects.filter(
            result__in=runs,
            result__project_id=project_id,
        ).values_list(
            'result__id',
            'meta__id',
        )

        metadata_categories = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'METADATA_ON_PAGES',
            project_id,
        )

        groupping_kwargs = {
            'meta_results': metadata_results,
            'categories': metadata_categories,
            'project_id': project_id,
            'groupby_fn': group_by_runs,
            'format_fn': key_value_list_transforming,
        }

        if categorize:
            groupping_kwargs.update({'groupby_fn': group_by_runs_and_category})

        metadata_by_runs.update(get_metas_by_category(**groupping_kwargs))

    return metadata_by_runs


def get_tags_by_runs(runs, not_categorize=False):
    """
    Prepare tags as following: {'run_id': ['tag_id', ], }.
    Runs items can represent TestIterationResult objects or just IDs.
    """

    run_project_map = dict(
        TestIterationResult.objects.filter(id__in=runs).values_list('id', 'project_id'),
    )

    run_meta_ids_map = defaultdict(set)
    meta_qs = MetaResult.objects.filter(result_id__in=runs).values_list('result_id', 'meta_id')
    for run_id, meta_id in meta_qs:
        run_meta_ids_map[run_id].add(meta_id)

    tags_cache_per_project = {}
    for project_id in set(run_project_map.values()):
        tags_cache = ProjectCache(project_id).tags
        if not any(tags_cache.get(k) for k in tags_cache.KEY_DATA_CHOICES):
            tags_cache.load()
        tags_cache_per_project[project_id] = tags_cache

    al_tags_by_run = {}
    important_tags_by_run = {}
    relevant_tags_by_run = {}

    for run_id, meta_ids in run_meta_ids_map.items():
        project_tags_cache = tags_cache_per_project[run_project_map[run_id]]

        if not_categorize:
            al_tags_by_run[run_id] = [
                value
                for tag_id, value in project_tags_cache.get('all').items()
                if tag_id in meta_ids
            ]
        else:
            important_tags_by_run[run_id] = [
                value
                for tag_id, value in project_tags_cache.get('important').items()
                if tag_id in meta_ids
            ]
            relevant_tags_by_run[run_id] = [
                value
                for tag_id, value in project_tags_cache.get('relevant').items()
                if tag_id in meta_ids
            ]

    return al_tags_by_run if not_categorize else (important_tags_by_run, relevant_tags_by_run)


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
