# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import OrderedDict
import logging
import re
import sys

from django.conf import settings
from django.db.models import Exists, F, OuterRef, Q
import per_conf

from references import References

from bublik.core.cache import RunCache
from bublik.core.datetime_formatting import (
    display_to_date_in_numbers,
    display_to_seconds,
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
    Meta,
    MetaResult,
    ResultType,
    RunConclusion,
    RunStatusByUnexpected,
    TestIterationRelation,
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


def generate_result(test_iter_res, parent, period, path, info, run_results):
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
                    run_results,
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
                run_results,
            )

    if info:
        info['children'].append(test_iter_res_info)
        for result in info['stats']:
            info['stats'][result] += test_iter_res_info['stats'][result]
    else:
        info = test_iter_res_info

    return info


def get_run_stats_detailed(run_id):
    cache = RunCache.by_id(run_id, 'stats')
    run_stats = cache.data
    # Recalculating statistics if it is not stored in the cache
    if not run_stats:
        run_results = (
            TestIterationResult.objects.filter(test_run=run_id)
            .order_by('start')
            .select_related('iteration__test')
        )
        main_package = run_results.filter(parent_package__isnull=True).first()
        if not main_package:
            return None
        run_stats = generate_result(
            test_iter_res=main_package,
            parent=None,
            period=(main_package.start, main_package.finish),
            path=[],
            info=None,
            run_results=run_results,
        )
        cache.data = run_stats
    return run_stats


def get_packages_stats(data):
    if data['type'] != 'pkg':
        return

    yield {
        'path': data['path'],
        'stats': data['stats'],
    }

    for child in data['children']:
        yield from get_packages_stats(child)


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


def get_runs_summary(
    start_date,
    finish_date,
    filter_tags=None,
    page_options=None,
    runs_limit=None,
):
    page, max_page_items = page_options if page_options else (1, sys.maxsize)

    runs = get_test_runs(start_date=start_date, finish_date=finish_date, tags=filter_tags)

    tags = MetaResult.objects.filter(meta__type='tag').prefetch_related('meta')

    runs_count = runs.count()

    limit = runs_limit if runs_limit and runs_limit > runs_count else runs_count

    border_chosen = limit if page * max_page_items > limit else page * max_page_items

    runs_data = []

    for run in runs.all()[(page - 1) * max_page_items : border_chosen]:
        run_data = {}

        run_tags = tags.filter(result=run).order_by('meta__name')

        run_data['tags_important'] = (
            run_tags.filter(meta__category__priority__range=(1, 3))
            .values('meta__name', 'meta__value')
            .annotate(name=F('meta__name'), value=F('meta__value'))
        )
        run_data['tags'] = (
            run_tags.filter(
                Q(meta__category__priority__exact=4) | Q(meta__category__isnull=True),
            )
            .values('meta__name', 'meta__value')
            .annotate(name=F('meta__name'), value=F('meta__value'))
        )

        run_data['start_date'] = display_to_seconds(run.start)
        run_data['start_date_iso'] = display_to_date_in_numbers(run.start)

        run_data['finish_date'] = display_to_seconds(run.finish)
        run_data['finish_date_iso'] = display_to_date_in_numbers(run.finish)

        run_data['id'] = run.id

        runs_data.append(run_data)

    return runs_count, runs_data


def get_test_path(result_id):
    '''
    Get path to the test with result_id.
    '''
    packages = (
        TestIterationRelation.objects.filter(test_iteration__testiterationresult=result_id)
        .order_by('-depth')
        .values('parent_iteration__test__name')
    )

    return '/'.join(package['parent_iteration__test__name'] for package in packages)


def get_expected_results(result):
    expected_results = []
    for expectation in result.expectations.all():
        meta_expect = expectation.expectmeta_set.all()
        expected_result = {
            'result': None,
            'verdicts': [],
            'key': [],
        }

        meta_expect_results = meta_expect.filter(meta__type='result')
        if not meta_expect_results.exists():
            continue
        expected_result['result'] = meta_expect_results.first().meta.value

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
                    expected_result['key'].append(key_part)

                # Parse the ref
                ref_type, ref_tail = re.search(r'ref://(.*)/(.*)', ref).group(1, 2)

                # Forming the ref name
                ref_name = f'{ref_type}:{ref_tail}'
                key_part = {'name': ref_name, 'url': None}

                # Form the link address, if possible
                if ref_type in References.logs and ref_tail:
                    ref_uri = References.logs[ref_type]['uri'][0]
                    ref_url = f'{ref_uri}{ref_tail}'
                    key_part['url'] = ref_url

                expected_result['key'].append(key_part)

                # Trim the key string by the current ref
                key_string = key_string.partition(ref)[2]

            # Add what is left in the key string
            if key_string:
                key_part = {'name': key_string, 'url': None}
                expected_result['key'].append(key_part)

        expected_results.append(expected_result)
    return expected_results


def generate_stats(child_results, unexpected_result_type):
    iterations_stats = []
    for child_result in child_results:
        child_id = child_result.id
        test_name = child_result.iteration.test.name
        test_stats = {
            'iteration_id': child_result.iteration.id,
            'result_id': child_id,
            'hash': child_result.iteration.hash,
            'name': test_name,
            'history_params': None,
            'has_measurements': None,
            'obtained_results': {'result': None, 'verdicts': []},
            'expected_results': [],
            'comment': None,
            'parameters': None,
        }

        meta_results = child_result.meta_results.filter(meta__type='result')
        if meta_results.exists():
            test_stats['obtained_results']['result'] = meta_results.first().meta.value

            # If the test executed as expect,
            # or the obtained result is different from the one passed as a filter,
            # skip the iteration
            if unexpected_result_type:
                unexpected = child_result.meta_results.filter(meta__type='err').exists()
                if (
                    not unexpected
                    or unexpected_result_type
                    != test_stats['obtained_results']['result'].lower()
                ):
                    continue

        meta_results = child_result.meta_results.filter(meta__type='verdict')
        if meta_results.exists():
            test_stats['obtained_results']['verdicts'] = list(
                meta_results.all().order_by('serial').values_list('meta__value', flat=True),
            )

        test_stats['expected_results'] = get_expected_results(child_result)

        meta_results = child_result.meta_results.filter(meta__type='note')
        if meta_results.exists():
            test_stats['comment'] = meta_results.first().meta.value

        parameters = (
            child_result.iteration.test_arguments.all()
            .values_list('name', 'value')
            .order_by('name')
        )

        test_stats['parameters'] = dict(parameters)

        # Due to a circular import
        from bublik.core.history.v1.utils import default_history_params

        params = default_history_params(
            start=child_result.start.date(),
            add_params={
                'test_name': test_name,
                'parameters': settings.QUERY_DELIMITER.join(
                    key_value_list_transforming(parameters, settings.KEY_VALUE_DELIMITER),
                ),
            },
        )

        test_stats['history_params'] = params

        is_measurements = exist_measurement_results(child_result)
        test_stats['has_measurements'] = is_measurements

        iterations_stats.append(test_stats)
    return iterations_stats


def get_all_unexpected_results_related(result):
    tests = get_children(
        parent=result,
        q=Q(iteration__hash__isnull=False, meta_results__meta__type='err'),
    )
    packages = get_children(parent=result, q=Q(iteration__hash__isnull=True))
    for pkg in packages:
        tests |= get_all_unexpected_results_related(pkg)
    return tests.all()


def get_iterations_stats(result_id, unexpected_result_type):
    '''This function gives statistics of unexpected results for a given package
    or session. Since it may not be a direct parent of unexpected test results
    its desendants and their desendants are considering.
    '''
    if not unexpected_result_type:
        return None
    result = TestIterationResult.objects.get(id=result_id)
    unexpected_results = get_all_unexpected_results_related(result)
    return {'results': generate_stats(unexpected_results, unexpected_result_type)}


def get_child_iterations_stats(parent_id, test_name, period, unexpected_result_type=None):
    '''This function gives statistics for a given test set.'''
    parent_test_result = get_or_none(TestIterationResult.objects, pk=parent_id)
    if not parent_test_result:
        return None

    finish_filter = {}
    if period[1] is not None:
        finish_filter['finish__lte'] = period[1]

    child_results = (
        TestIterationResult.objects.filter(
            parent_package=parent_test_result,
            iteration__test__name=test_name,
            iteration__hash__isnull=False,
            start__gte=period[0],
            **finish_filter,
        )
        .order_by('start')
        .select_related('iteration__test')
        .all()
    )

    return {'results': generate_stats(child_results, unexpected_result_type)}


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

        # Handle expected result
        expected_results = get_expected_results(test_result)
        expected_result = expected_results[0]
        expected_result_data = {
            'result_type': expected_result['result'],
            'verdict': expected_result['verdicts'],
            'key': expected_result['key'],
        }

        # Handle obtained result and comments
        result_type = None
        verdict = []
        comments = []

        for meta_result in test_result.meta_results.all():
            if meta_result.meta.type == 'result':
                result_type = meta_result.meta.value
            elif meta_result.meta.type == 'verdict':
                verdict.append(meta_result.meta.value)
            elif meta_result.meta.type == 'note':
                comments.append(meta_result.meta.value)

        obtained_result_data = {
            'result_type': result_type,
            'verdict': verdict,
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
            'iteration_id': iteration_id,
            'start': test_result.start,
            'obtained_result': obtained_result_data,
            'expected_result': expected_result_data,
            'parameters': parameters_list,
            'comments': comments,
            'has_error': is_result_unexpected(test_result),
            'has_measurements': exist_measurement_results(test_result),
        }

        results_details.append(data)

    return results_details


def generate_result_details(test_result):
    return generate_results_details([test_result])[0]


def get_run_status(run):
    status_meta_name = getattr(per_conf, 'RUN_STATUS_META', None)
    status_meta = MetaResult.objects.filter(result=run, meta__name=status_meta_name).first()
    if status_meta:
        return status_meta.meta.value
    return None


def get_run_status_by_nok(run):
    stats = get_run_stats(run.id)
    return RunStatusByUnexpected.identify(stats)


def get_driver_unload(run):
    du_meta = MetaResult.objects.filter(result=run, meta__name='driver_unload').first()
    return du_meta.meta.value if du_meta else None


def get_run_conclusion(run):
    run_id = run.id
    status = get_run_status(run_id)
    compromised = is_run_compromised(run)
    status_by_nok = get_run_status_by_nok(run)
    driver_unload = get_driver_unload(run)
    return RunConclusion.identify(status, status_by_nok, compromised, driver_unload)


def generate_all_run_details(run):
    logger.debug('[run_details]: enter')
    run_id = run.id
    important_tags, relevant_tags = get_tags_by_runs([run_id])
    category_names = getattr(per_conf, 'SPECIAL_CATEGORIES', [])
    run_meta_results = run.meta_results.select_related('meta')
    q = MetaResultsQuery(run_meta_results)

    branches = q.metas_query('branch')
    revisions = build_revision_references(q.metas_query('revision'))
    labels = q.labels_query(excluding_category_names=category_names)
    categories = get_metas_by_category(run_meta_results, category_names)
    for category, category_values in categories.items():
        categories[category] = key_value_list_transforming(category_values)

    logger.debug('[run_details]: preparing resulting dict')
    return {
        'id': run_id,
        'start': run.start,
        'finish': run.finish,
        'duration': run.duration,
        'main_package': run.main_package.iteration.test.name if run.main_package else None,
        'status': get_run_status(run_id),
        'status_by_nok': get_run_status_by_nok(run),
        'compromised': get_compromised_details(run),
        'conclusion': get_run_conclusion(run),
        'important_tags': important_tags.get(run_id, []),
        'relevant_tags': relevant_tags.get(run_id, []),
        'branches': key_value_list_transforming(branches),
        'revisions': revisions,
        'labels': key_value_list_transforming(labels),
        'special_categories': categories,
    }


def generate_runs_details(runs):
    important_tags, relevant_tags = get_tags_by_runs(runs)
    metadata_by_runs = get_metadata_by_runs(runs)

    runs_data = []
    for run in runs:
        run_id = run.id
        runs_data.append(
            {
                'id': run_id,
                'start': run.start,
                'finish': run.finish,
                'duration': run.duration,
                'status': get_run_status(run_id),
                'status_by_nok': get_run_status_by_nok(run),
                'compromised': is_run_compromised(run),
                'conclusion': get_run_conclusion(run),
                'metadata': metadata_by_runs.get(run_id, []),
                'important_tags': important_tags.get(run_id, []),
                'relevant_tags': relevant_tags.get(run_id, []),
                'stats': get_run_stats_summary(run_id),
            },
        )

    return runs_data
