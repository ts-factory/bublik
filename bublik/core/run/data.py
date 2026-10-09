# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict, defaultdict

from django.db import connection, transaction
from django.db.models import Exists

from bublik.core.cache import ProjectCache
from bublik.core.classification import suppressed_subquery
from bublik.core.config.services import ConfigServices
from bublik.core.meta.categorization import (
    get_metas_by_category,
    group_by_runs,
    group_by_runs_and_category,
)
from bublik.core.utils import key_value_list_transforming
from bublik.data.models import (
    GlobalConfigs,
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

    runs = [run.id if hasattr(run, 'id') else run for run in runs]

    run_project_map = dict(
        TestIterationResult.objects.filter(id__in=runs).values_list('id', 'project_id'),
    )

    run_meta_ids_map = defaultdict(set)
    meta_qs = MetaResult.objects.filter(result_id__in=runs).values_list('result_id', 'meta_id')
    for run_id, meta_id in meta_qs:
        run_meta_ids_map[run_id].add(meta_id)

    metas_cache_per_project = {}
    for project_id in set(run_project_map.values()):
        metas_cache = ProjectCache(project_id).metas
        if not any(metas_cache.get(k) for k in metas_cache.KEY_DATA_CHOICES):
            metas_cache.load()
        metas_cache_per_project[project_id] = metas_cache

    all_tags_by_run = {}
    important_tags_by_run = {}
    relevant_tags_by_run = {}

    for run_id, meta_ids in run_meta_ids_map.items():
        project_metas_cache = metas_cache_per_project[run_project_map[run_id]]

        if not_categorize:
            all_tags_by_run[run_id] = [
                value
                for tag_id, value in project_metas_cache.get('all_tags').items()
                if tag_id in meta_ids
            ]
        else:
            important_tags_by_run[run_id] = [
                value
                for tag_id, value in project_metas_cache.get('important_tags').items()
                if tag_id in meta_ids
            ]
            relevant_tags_by_run[run_id] = [
                value
                for tag_id, value in project_metas_cache.get('relevant_tags').items()
                if tag_id in meta_ids
            ]

    return all_tags_by_run if not_categorize else (important_tags_by_run, relevant_tags_by_run)


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
    # An error that is not suppressed by an expected rule on an open issue.
    return (
        TestIterationResult.objects.filter(pk=result.pk, meta_results__meta__type='err')
        .exclude(Exists(suppressed_subquery()))
        .exists()
    )


# For every group of expected results of a test iteration result with the same
# result and verdicts, the links to the expected results contained in another
# one of the group from another import, and whether the links left include
# links to expected results of different imports.
#
# An expected result is contained in another one if each of its expect metas
# (meta, serial and reference) is also an expect meta of the other one, and
# the other one has an expect meta of its own. The latter keeps both of two
# expected results with the same expect metas.
#
# Two links of a test iteration result come from different imports if their
# expected results have different tag expressions, or if links of other test
# iteration results of the run lie between them: the difference of their
# positions among the links of the run then exceeds the difference of their
# positions among the links of the test iteration result.
#
# Containment depends only on the expected results, and the same expected
# results are linked to many test iteration results, so it is checked once
# per pair of them.
DUPLICATES_QUERY = """
WITH run_links AS (
    SELECT er.id, er.expectation_id, er.testiterationresult_id, r.test_run_id,
           row_number() OVER (PARTITION BY r.test_run_id ORDER BY er.id) AS run_pos,
           row_number() OVER (
               PARTITION BY er.testiterationresult_id ORDER BY er.id
           ) AS result_pos
    FROM bublik_expectation_results er
    JOIN bublik_testiterationresult r ON r.id = er.testiterationresult_id
    WHERE r.test_run_id = ANY(%s)
),
expectation_signatures AS (
    SELECT em.expectation_id,
           string_agg(m.value, '') FILTER (WHERE m.type = 'result') AS status,
           array_agg(m.value ORDER BY em.serial)
               FILTER (WHERE m.type = 'verdict_expected') AS verdicts,
           max(m.value) FILTER (WHERE m.type = 'tag_expression') AS tag_expression
    FROM bublik_expectmeta em
    JOIN bublik_meta m ON m.id = em.meta_id
    WHERE em.expectation_id IN (SELECT expectation_id FROM run_links)
    GROUP BY em.expectation_id
),
grouped_links AS (
    SELECT l.id, l.expectation_id, l.testiterationresult_id, l.test_run_id,
           l.run_pos, l.result_pos, s.tag_expression,
           dense_rank() OVER (
               PARTITION BY l.testiterationresult_id ORDER BY s.status, s.verdicts
           ) AS group_no,
           count(*) OVER (
               PARTITION BY l.testiterationresult_id, s.status, s.verdicts
           ) AS group_size
    FROM run_links l
    JOIN expectation_signatures s ON s.expectation_id = l.expectation_id
),
duplicate_links AS (
    SELECT id, expectation_id, testiterationresult_id, test_run_id,
           run_pos, result_pos, tag_expression, group_no
    FROM grouped_links
    WHERE group_size > 1
),
link_pairs AS (
    SELECT d.id AS inner_link_id,
           o.id AS outer_link_id,
           d.expectation_id AS inner_id,
           o.expectation_id AS outer_id,
           d.tag_expression IS DISTINCT FROM o.tag_expression
               OR o.run_pos - d.run_pos <> o.result_pos - d.result_pos
               AS other_import
    FROM duplicate_links d
    JOIN duplicate_links o
      ON o.testiterationresult_id = d.testiterationresult_id
     AND o.group_no = d.group_no
     AND o.id <> d.id
),
contained_pairs AS (
    SELECT p.inner_id, p.outer_id
    FROM (
        SELECT DISTINCT inner_id, outer_id FROM link_pairs WHERE other_import
    ) p
    WHERE NOT EXISTS (
        SELECT 1
        FROM bublik_expectmeta i
        WHERE i.expectation_id = p.inner_id
          AND NOT EXISTS (
              SELECT 1
              FROM bublik_expectmeta o
              WHERE o.expectation_id = p.outer_id
                AND o.meta_id = i.meta_id
                AND o.serial = i.serial
                AND o.reference_id IS NOT DISTINCT FROM i.reference_id
          )
    )
    AND EXISTS (
        SELECT 1
        FROM bublik_expectmeta o
        WHERE o.expectation_id = p.outer_id
          AND NOT EXISTS (
              SELECT 1
              FROM bublik_expectmeta i
              WHERE i.expectation_id = p.inner_id
                AND i.meta_id = o.meta_id
                AND i.serial = o.serial
                AND i.reference_id IS NOT DISTINCT FROM o.reference_id
          )
    )
),
contained_links AS (
    SELECT DISTINCT p.inner_link_id AS id
    FROM link_pairs p
    JOIN contained_pairs c ON c.inner_id = p.inner_id AND c.outer_id = p.outer_id
    WHERE p.other_import
),
mixed_groups AS (
    SELECT DISTINCT d.testiterationresult_id, d.group_no
    FROM link_pairs p
    JOIN duplicate_links d ON d.id = p.inner_link_id
    LEFT JOIN contained_links ci ON ci.id = p.inner_link_id
    LEFT JOIN contained_links co ON co.id = p.outer_link_id
    WHERE p.other_import AND ci.id IS NULL AND co.id IS NULL
)
SELECT d.test_run_id,
       d.testiterationresult_id,
       coalesce(array_agg(d.id ORDER BY d.id) FILTER (WHERE c.id IS NOT NULL), '{}')
           AS link_ids,
       coalesce(
           array_agg(d.expectation_id ORDER BY d.id) FILTER (WHERE c.id IS NOT NULL), '{}'
       ) AS expectation_ids,
       bool_or(g.group_no IS NOT NULL) AS mixed
FROM duplicate_links d
LEFT JOIN contained_links c ON c.id = d.id
LEFT JOIN mixed_groups g
  ON g.testiterationresult_id = d.testiterationresult_id AND g.group_no = d.group_no
GROUP BY d.testiterationresult_id, d.test_run_id, d.group_no
"""


def find_duplicates(run_ids):
    """
    For every group of duplicated expected results of the given runs, yield
    the run ID, the test iteration result ID, the IDs of the links to the
    expected results contained in another one of the group from another import
    and the IDs of those expected results, and whether expected results of
    different imports that carry data of their own are left in the group.
    """
    # A server-side cursor, so that millions of groups are not loaded at once
    with transaction.atomic(), connection.chunked_cursor() as cursor:
        cursor.execute(DUPLICATES_QUERY, [run_ids])
        yield from cursor
