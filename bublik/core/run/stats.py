# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict
import json
import logging
import re

from django.conf import settings
from django.contrib.postgres.aggregates import ArrayAgg
from django.db import models
from django.db.models import Exists, F, OuterRef, Q, Value
from django.db.models.functions import Concat

from bublik.core.cache import RunCache
from bublik.core.config.services import ConfigServices
from bublik.core.datetime_formatting import (
    period_to_str,
)
from bublik.core.measurement.services import exist_measurement_results
from bublik.core.meta.categorization import get_metas_by_category
from bublik.core.meta.match_references import build_revision_references
from bublik.core.queries import MetaResultsQuery, get_or_none
from bublik.core.run.compromised import get_compromised_details, is_run_compromised
from bublik.core.run.data import (
    get_metadata_by_runs,
    get_tags_by_runs,
    is_result_unexpected,
)
from bublik.core.run.filter_expression import filter_by_expression
from bublik.core.utils import key_value_dict_transforming, key_value_list_transforming
from bublik.data.models import (
    GlobalConfigs,
    Meta,
    MetaResult,
    MetaTest,
    ResultType,
    RunConclusion,
    RunStatusByUnexpected,
    TestIterationResult,
)


logger = logging.getLogger('bublik.server')


def get_children(parent, test_results=TestIterationResult.objects, q=None):
    query = Q(parent_package=parent)
    if q:
        query &= q
    return test_results.filter(query).order_by('start')


def passed(results):
    return results.filter(meta_results__meta=Meta.passed)


def failed(results):
    return results.filter(meta_results__meta=Meta.failed)


def skipped(results):
    return results.filter(meta_results__meta=Meta.skipped)


def abnormal(results):
    return results.filter(meta_results__meta__in=Meta.abnormal)


def generate_result(
    test_iter_res,
    parent,
    period,
    path,
    info,
    objectives,
    run_results,
    available_req_metas,
):
    test = test_iter_res.iteration.test
    test_name = test.name
    path = [*path, test_name]
    parent_id = test_iter_res.parent_package.id if test_iter_res.parent_package else None

    test_iter_res_info = {
        'result_id': test.id,
        'iteration_id': test_iter_res.id,
        'exec_seqno': test_iter_res.exec_seqno,
        'parent_id': parent_id,
        'type': test.get_result_type_display(),
        'name': test_name,
        'period': period_to_str(period),
        'path': path,
        'objective': objectives.get(test_iter_res.id, ''),
        'children': [],
        'stats': {
            'passed': 0,
            'failed': 0,
            'passed_unexpected': 0,
            'failed_unexpected': 0,
            'skipped': 0,
            'skipped_unexpected': 0,
            'abnormal': 0,
        },
    }

    if ResultType.inv(test_iter_res.iteration.test.result_type) == ResultType.TEST:
        finish_filter = {}
        if period[1] is not None:
            finish_filter['finish__lte'] = period[1]

        test_iterations = run_results.filter(
            iteration__test__name=test_iter_res.iteration.test.name,
            parent_package=parent,
            start__gte=period[0],
            **finish_filter,
        )

        # filter tests by requirements
        for req_meta in available_req_metas:
            test_iterations = test_iterations.filter(meta_results__meta=req_meta)

        if not test_iterations:
            test_iter_res_info = None
        else:
            all_passed = passed(test_iterations).count()
            all_failed = failed(test_iterations).count()
            all_skipped = skipped(test_iterations).count()
            all_abnormal = abnormal(test_iterations).count()

            unexpected = test_iterations.filter(meta_results__meta__type='err')

            passed_unexpected = passed(unexpected).distinct().count()
            failed_unexpected = failed(unexpected).distinct().count()
            skipped_unexpected = skipped(unexpected).distinct().count()

            test_iter_res_info['stats']['passed'] = all_passed - passed_unexpected
            test_iter_res_info['stats']['failed'] = all_failed - failed_unexpected
            test_iter_res_info['stats']['passed_unexpected'] = passed_unexpected
            test_iter_res_info['stats']['failed_unexpected'] = failed_unexpected
            test_iter_res_info['stats']['skipped'] = all_skipped - skipped_unexpected
            test_iter_res_info['stats']['skipped_unexpected'] = skipped_unexpected
            test_iter_res_info['stats']['abnormal'] = all_abnormal

    else:
        children = run_results.filter(parent_package=test_iter_res)

        prev_child = None
        for child in children:
            if prev_child:
                prev_child_name = prev_child['test_iter_obj'].iteration.test.name
                child_name = child.iteration.test.name
                child_result_type = child.iteration.test.result_type
                if prev_child_name == child_name and child_result_type == ResultType.conv(
                    ResultType.TEST,
                ):
                    prev_child['finish'] = child.finish
                    continue

                generate_result(
                    prev_child['test_iter_obj'],
                    test_iter_res,
                    (prev_child['start'], prev_child['finish']),
                    path,
                    prev_child['info'],
                    objectives,
                    run_results,
                    available_req_metas,
                )

            prev_child = {
                'test_iter_obj': child,
                'info': test_iter_res_info,
                'start': child.start,
                'finish': child.finish,
            }

        if prev_child:
            generate_result(
                prev_child['test_iter_obj'],
                test_iter_res,
                (prev_child['start'], prev_child['finish']),
                path,
                prev_child['info'],
                objectives,
                run_results,
                available_req_metas,
            )

        if sum(test_iter_res_info['stats'].values()) == 0:
            test_iter_res_info = None

    if info:
        if test_iter_res_info:
            info['children'].append(test_iter_res_info)
            for result in info['stats']:
                info['stats'][result] += test_iter_res_info['stats'][result]
    else:
        info = test_iter_res_info

    return info


def get_run_stats_detailed_with_comments(run_id, requirements):
    run_stats = get_run_stats_detailed(run_id, requirements)
    tests_comments = get_tests_comments(run_id)
    add_comments(run_stats, tests_comments)
    return run_stats


def add_comments(node, tests_comments):
    '''
    Add a list of comments to each node. The comment format is
    {
        'comment_id': <meta_id>,
        'updated': <metatest_updated>,
        'serial': <metatest_serial>,
        'comment': <meta_value>
    }
    The order of comments is determined by the serial value.
    '''
    if node:
        node['comments'] = tests_comments.get(node['result_id'], [])
        node['comments'] = [
            json.loads(comment) if isinstance(comment, str) else comment
            for comment in node['comments']
        ]
        node['comments'] = sorted(node['comments'], key=lambda x: x['serial'])
        for child in node['children']:
            add_comments(child, tests_comments)


def get_run_stats_detailed(run_id, requirements=None):
    requirements = requirements or {}
    if requirements:
        requirements = set(requirements.split(settings.QUERY_DELIMITER))
        stats_cache = RunCache.by_id(run_id, 'stats_reqs')
        run_stats = stats_cache.data.get('stats') if stats_cache.data else None
        requirements_cached = stats_cache.data.get('reqs') if stats_cache.data else None
    else:
        stats_cache = RunCache.by_id(run_id, 'stats')
        run_stats = stats_cache.data
    # Recalculate statistics if they are not cached or do not match the given requirements
    if not run_stats or (requirements and requirements != requirements_cached):
        run_results = (
            TestIterationResult.objects.filter(test_run=run_id)
            .order_by('start')
            .select_related('iteration__test')
        )
        main_package = run_results.filter(parent_package__isnull=True).first()
        if not main_package:
            return None

        # get objectives for all run iterations at once
        objectives = dict(
            Meta.objects.filter(
                metaresult__result__test_run=run_id,
                type='objective',
            ).values_list('metaresult__result__id', 'value'),
        )

        # get metadata matching passed requirements for further test filtering
        available_req_metas = []
        for requirement in requirements:
            try:
                available_req_metas.append(
                    Meta.objects.get(type='requirement', value=requirement),
                )
            except Meta.DoesNotExist:
                return None

        run_stats = generate_result(
            test_iter_res=main_package,
            parent=None,
            period=(main_package.start, main_package.finish),
            path=[],
            info=None,
            objectives=objectives,
            run_results=run_results,
            available_req_metas=available_req_metas,
        )

        stats_cache.data = (
            run_stats if not requirements else {'reqs': requirements, 'stats': run_stats}
        )

    return run_stats


def get_tests_comments(run_id):
    '''
    Return all run test comments in the format
    {
        'test_id': [
            {
                'metatest_id': <metatest_id>,
                'metatest__serial': <metatest__serial>,
                'value': <meta_value>,
            }, ...
        ], ...
    }
    '''
    test_ids = list(
        (
            TestIterationResult.objects.filter(test_run=run_id)
            .order_by('start')
            .select_related('iteration__test')
        ).values_list('iteration__test__id', flat=True),
    )
    project_id = (
        TestIterationResult.objects.filter(id=run_id)
        .values_list('project__id', flat=True)
        .get()
    )
    return dict(
        MetaTest.objects.filter(
            meta__type='comment',
            test__id__in=test_ids,
            project_id=project_id,
        )
        .values('test__id')
        .annotate(
            comment_list=ArrayAgg(
                Concat(
                    Value('{"comment_id": "'),
                    F('meta__id'),
                    Value('", "updated": "'),
                    F('updated'),
                    Value('", "serial": "'),
                    F('serial'),
                    Value('", "comment": '),
                    F('meta__value'),
                    Value('}'),
                    output_field=models.JSONField(),
                ),
            ),
        )
        .values_list('test__id', 'comment_list'),
    )


def get_test_runs(
    start_date=None,
    finish_date=None,
    tags=None,
    exclude_label=None,
    order_by='-start',
    escape_compromised=True,
):
    '''
    Get test runs filtered by date and tags with possibility to exclude runs
    which contain `exclude_label` in meta labels.
    '''

    runs = TestIterationResult.objects.filter(test_run=None)
    if escape_compromised:
        runs = runs.exclude(meta_results__meta__name='compromised')
    if start_date:
        runs = runs.filter(start__date__gte=start_date)
    if finish_date:
        runs = runs.filter(finish__date__lte=finish_date)

    if tags:
        runs = runs.values('id')
        runs = filter_by_expression(runs, tags)
        runs = TestIterationResult.objects.filter(id__in=runs)

    if exclude_label:
        runs = runs.exclude(
            meta_results__meta__type='label',
            meta_results__meta__value__contains=exclude_label,
        )

    if order_by:
        runs = runs.order_by(order_by)

    return runs


def get_run_stats(run_id):
    cache = RunCache.by_id(run_id, 'stats_sum')
    stats = cache.data
    if not stats:
        stats = {}
        run_results = TestIterationResult.objects.filter(
            test_run=run_id,
            iteration__hash__isnull=False,
        )
        stats['total'] = run_results.count()
        stats['unexpected'] = run_results.filter(meta_results__meta__type='err').count()

        plan_meta_result = get_or_none(
            MetaResult.objects,
            result__id=run_id,
            meta__name='expected_items',
            meta__type='count',
        )
        if plan_meta_result is not None:
            plan_tests_num = int(plan_meta_result.meta.value)
            stats['total_expected'] = plan_tests_num

            prologues_not_passed_tests_nums = Meta.objects.filter(
                metaresult__result__test_run=run_id,
                name='expected_items_prologue',
            ).values_list('value', flat=True)
            prologues_not_passed_sum = sum([int(i) for i in prologues_not_passed_tests_nums])
            actual_tests_num = run_results.count() + prologues_not_passed_sum
            stats['progress'] = actual_tests_num / plan_tests_num

        cache.data = stats
    return stats


def get_run_stats_summary(run_id):
    results = get_run_stats(run_id)

    if not results:
        return None

    tests_total = results['total']
    tests_total_nok = results['unexpected']
    tests_total_ok = tests_total - tests_total_nok

    try:
        tests_total_plan_percent = round(results['progress'] * 100)
    except KeyError:
        tests_total_plan_percent = None

    if tests_total != 0:
        tests_total_ok_percent = round(tests_total_ok * 100 / tests_total)
        tests_total_nok_percent = round(tests_total_nok * 100 / tests_total)
    else:
        tests_total_ok_percent = 0
        tests_total_nok_percent = 0

    if not tests_total_nok_percent and tests_total_nok:
        tests_total_nok_percent += 1
        tests_total_ok_percent -= 1
    elif not tests_total_ok_percent and tests_total_ok:
        tests_total_ok_percent += 1
        tests_total_nok_percent -= 1

    return {
        'tests_total': tests_total,
        'tests_total_plan_percent': tests_total_plan_percent,
        'tests_total_ok': tests_total_ok,
        'tests_total_ok_percent': tests_total_ok_percent,
        'tests_total_nok': tests_total_nok,
        'tests_total_nok_percent': tests_total_nok_percent,
    }


def get_expected_results(result):
    expected_results = []
    for expectation in result.expectations.all():
        meta_expect = expectation.expectmeta_set.all()
        expected_result = {
            'result_type': None,
            'verdicts': [],
            'keys': [],
        }

        meta_expect_results = meta_expect.filter(meta__type='result')
        if not meta_expect_results.exists():
            continue
        expected_result['result_type'] = meta_expect_results.first().meta.value

        meta_expect_results = meta_expect.filter(meta__type='verdict_expected')
        if meta_expect_results.exists():
            expected_result['verdicts'] = list(
                meta_expect_results.all()
                .order_by('serial')
                .values_list('meta__value', flat=True),
            )

        meta_expect_results = meta_expect.filter(meta__type='key')
        if meta_expect_results.exists():
            key_string = meta_expect_results.first().meta.name

            for ref in re.findall(r'ref://[^, ]+', key_string):
                # Add the information that is before the first ref
                key_info_part = key_string.partition(ref)[0]
                if key_info_part:
                    key_part = {'name': key_info_part, 'url': None}
                    expected_result['keys'].append(key_part)

                # Parse the ref
                ref_type, ref_tail = re.search(r'ref://(.*)/(.*)', ref).group(1, 2)

                # Forming the ref name
                ref_name = f'{ref_type}:{ref_tail}'
                key_part = {'name': ref_name, 'url': None}

                # Form the link address, if possible
                logs = ConfigServices.getattr_from_global(
                    GlobalConfigs.REFERENCES.name,
                    'ISSUES',
                    result.project.id,
                )
                if ref_type in logs and ref_tail:
                    ref_uri = logs[ref_type]['uri']
                    ref_url = f'{ref_uri}{ref_tail}'
                    key_part['url'] = ref_url

                expected_result['keys'].append(key_part)

                # Trim the key string by the current ref
                key_string = key_string.partition(ref)[2]

            # Add what is left in the key string
            if key_string:
                key_part = {'name': key_string, 'url': None}
                expected_result['keys'].append(key_part)

        expected_results.append(expected_result)
    return expected_results


def get_nok_results_distribution(run):
    unexpected_result = MetaResult.objects.filter(result__id=OuterRef('id'), meta__type='err')
    return (
        TestIterationResult.objects.filter(
            test_run=run,
            iteration__test__result_type=ResultType.conv('test'),
        )
        .annotate(is_nok=Exists(unexpected_result))
        .values_list('is_nok', flat=True)
        .order_by('id')
    )


def generate_results_details(test_results):
    # Gather all results details
    results_details = []
    for test_result in test_results:
        result_id = test_result.id
        iteration = test_result.iteration
        iteration_id = iteration.id
        project = test_result.project

        # Handle expected result
        expected_results_data = get_expected_results(test_result)

        # Handle obtained result and comments
        result_type = None
        verdicts = []
        artifacts = []
        comments = []
        requirements = []

        for meta_result in test_result.meta_results.all():
            if meta_result.meta.type == 'result':
                result_type = meta_result.meta.value
            elif meta_result.meta.type == 'verdict':
                verdicts.append(meta_result.meta.value)
            elif meta_result.meta.type == 'artifact':
                artifacts.append(meta_result.meta.value)
            elif meta_result.meta.type == 'note':
                comments.append(meta_result.meta.value)
            elif meta_result.meta.type == 'requirement':
                requirements.append(meta_result.meta.value)

        obtained_result_data = {
            'result_type': result_type,
            'verdicts': verdicts,
        }

        # Handle parameters
        parameters = {}
        for test_argument in test_result.iteration.test_arguments.all():
            parameters[test_argument.name] = test_argument.value
        parameters = OrderedDict(sorted(parameters.items()))
        parameters_list = key_value_dict_transforming(parameters)

        data = {
            'name': iteration.test.name,
            'result_id': result_id,
            'run_id': test_result.root.id,
            'project_id': project.id,
            'project_name': project.name,
            'iteration_id': iteration_id,
            'start': test_result.start,
            'obtained_result': obtained_result_data,
            'expected_results': expected_results_data,
            'artifacts': artifacts,
            'parameters': parameters_list,
            'comments': comments,
            'requirements': requirements,
            'has_error': is_result_unexpected(test_result),
            'has_measurements': exist_measurement_results(test_result),
        }

        results_details.append(data)

    return results_details


def generate_result_details(test_result):
    return generate_results_details([test_result])[0]


def get_run_status(run):
    project_id = run.project.id
    status_meta_name = ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        'RUN_STATUS_META',
        project_id,
    )
    status_meta = MetaResult.objects.filter(result=run, meta__name=status_meta_name).first()
    if status_meta:
        return status_meta.meta.value
    return None


def get_run_status_by_nok(run):
    stats = get_run_stats(run.id)
    project_id = run.project.id
    return RunStatusByUnexpected.identify(stats, project_id)


def get_driver_unload(run):
    du_meta = MetaResult.objects.filter(result=run, meta__name='driver_unload').first()
    return du_meta.meta.value if du_meta else None


def get_run_conclusion(run):
    project_id = run.project.id
    status = get_run_status(run)
    compromised = is_run_compromised(run)
    status_by_nok, unexpected_percent = get_run_status_by_nok(run)
    driver_unload = get_driver_unload(run)
    return RunConclusion.identify(
        status,
        status_by_nok,
        unexpected_percent,
        compromised,
        driver_unload,
        project_id,
    )


def generate_all_run_details(run):
    logger.debug('[run_details]: enter')
    run_id = run.id
    project = run.project

    conclusion, conclusion_reason = get_run_conclusion(run)
    important_tags, relevant_tags = get_tags_by_runs([run_id])
    category_names = ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        'SPECIAL_CATEGORIES',
        project.id,
    )
    run_meta_results = run.meta_results.select_related('meta')
    q = MetaResultsQuery(run_meta_results)

    branches = q.metas_query('branch')
    revisions = build_revision_references(q.metas_query('revision'), project.id)
    labels = q.labels_query(category_names, project.id)
    configurations = list(
        key_value_list_transforming(
            get_metas_by_category(run_meta_results, ['Configuration'], project.id)[
                'Configuration'
            ],
        ),
    )
    categories = get_metas_by_category(run_meta_results, category_names, project.id)
    for category, category_values in categories.items():
        categories[category] = list(key_value_list_transforming(category_values))

    logger.debug('[run_details]: preparing resulting dict')
    return {
        'project_id': project.id,
        'project_name': project.name,
        'id': run_id,
        'start': run.start,
        'finish': run.finish,
        'duration': run.duration,
        'main_package': run.main_package.iteration.test.name if run.main_package else None,
        'status': get_run_status(run),
        'status_by_nok': get_run_status_by_nok(run)[0],
        'compromised': get_compromised_details(run),
        'conclusion': conclusion,
        'conclusion_reason': conclusion_reason,
        'important_tags': important_tags.get(run_id, []),
        'relevant_tags': relevant_tags.get(run_id, []),
        'branches': list(key_value_list_transforming(branches)),
        'revisions': revisions,
        'labels': list(key_value_list_transforming(labels)),
        'special_categories': categories,
        'configuration': configurations[0] if configurations else None,
    }


def generate_runs_details(runs):
    important_tags, relevant_tags = get_tags_by_runs(runs)
    metadata_by_runs = get_metadata_by_runs(runs)

    runs_data = []
    for run in runs:
        run_id = run.id
        conclusion, conclusion_reason = get_run_conclusion(run)
        runs_data.append(
            {
                'id': run_id,
                'project_id': run.project.id,
                'project_name': run.project.name,
                'start': run.start,
                'finish': run.finish,
                'duration': run.duration,
                'status': get_run_status(run),
                'status_by_nok': get_run_status_by_nok(run)[0],
                'compromised': is_run_compromised(run),
                'conclusion': conclusion,
                'conclusion_reason': conclusion_reason,
                'metadata': metadata_by_runs.get(run_id, []),
                'important_tags': important_tags.get(run_id, []),
                'relevant_tags': relevant_tags.get(run_id, []),
                'stats': get_run_stats_summary(run_id),
            },
        )

    return runs_data
