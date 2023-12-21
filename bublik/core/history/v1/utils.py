# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import defaultdict
from datetime import datetime, timedelta
import hashlib

from itertools import chain, groupby
import urllib

from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q

from bublik.core.datetime_formatting import display_to_milliseconds, get_duration
from bublik.core.run.stats import get_expected_results
from bublik.core.run.utils import prepare_dates_period
from bublik.data.models import ResultStatus, TestIterationResult


def create_metas_query(metas_str, types):
    count = 0
    query = Q()
    for meta in metas_str.split(settings.QUERY_DELIMITER):
        name, *value = meta.split('=', 1)
        query |= Q(name=name, value=value[0] if value else None, type__in=types)
        count += 1
    return query, count


def generate_hashkey(request):
    hashkey = hashlib.md5()

    for key, value in request.GET.items():
        if any(key == k for k in ('page', 'clicksearch', 'subsearch')) or not value:
            continue
        hashkey.update(f'{key}={value!s}%'.encode())

    return hashkey.hexdigest()


def prepare_list_results(
    test_name,
    test_results,
    important_tags,
    relevant_tags,
    metadata_by_runs,
    parameters_by_iterations,
    results,
    verdicts,
):

    results_to_response = []
    for test_result in test_results:
        run_id = test_result['run_id']
        iteration_id = test_result['iteration_id']
        result_id = test_result['id']

        # Handle obtained result
        result_type = results[result_id]

        # Handle verdicts
        verdicts_list = verdicts.get(result_id, [])

        # Handle parameters
        parameters_list = []
        parameters = parameters_by_iterations[iteration_id]
        for key, value in parameters.items():
            param_delimiter = settings.KEY_VALUE_DELIMITER
            parameters_list.append(f'{key}{param_delimiter}{value}')

        # Handle tags
        tags = relevant_tags.get(run_id, [])
        tags_important = important_tags.get(run_id, [])

        # Handle metadata
        metadata = metadata_by_runs.get(run_id, [])

        # Collect history params for the iteration
        history_params = default_history_params(
            start=test_result['start'].date(),
            results=[result_type],
            add_params={
                'test_name': test_name,
                'parameters': settings.QUERY_DELIMITER.join(parameters_list),
                'tags': settings.QUERY_DELIMITER.join(chain(tags, tags_important)),
                'metadata': settings.QUERY_DELIMITER.join(metadata),
                'verdict': settings.QUERY_DELIMITER.join(verdicts_list),
            },
        )

        # Get expected result
        expected_result_data = {}
        test_result_obj = TestIterationResult.objects.get(id=result_id)
        expected_results = get_expected_results(test_result_obj)
        if expected_results:
            expected_result = expected_results[0]
            expected_result_data = {expected_result['result']: expected_result['verdicts']}

        result_start = test_result['start']
        result_finish = test_result['finish']

        result = {
            'parameters': parameters,
            'obtained_result': {result_type: verdicts_list},
            'expected_result': expected_result_data,
            'has_error': test_result['has_error'],
            'run_id': str(run_id),
            'result_id': str(result_id),
            'iteration_id': str(iteration_id),
            'start_date': result_start,
            'finish_date': result_finish,
            'start_date_in_milliseconds': display_to_milliseconds(result_start),
            'duration': get_duration(result_start, result_finish),
            'tags': tags,
            'metadata': metadata,
            'important_tags': tags_important,
            'history_params': history_params,
            'has_measurements': test_result['is_measurements'],
        }

        results_to_response.append(result)

    return results_to_response


def group_results(test_results, tags_by_runs, parameters_by_iterations, results, verdicts):
    results_list = []
    tags_by_runs = dict(tags_by_runs)

    # Prepare list of results data
    for test_result in test_results:
        run_id = test_result['run_id']
        iteration_id = test_result['iteration_id']
        result_id = test_result['id']

        results_list.append(
            {
                'run_id': run_id,
                'result_id': result_id,
                'iteration_id': str(iteration_id),
                'start_date': display_to_milliseconds(test_result['start']),
                'hash': test_result['iteration_hash'],
                'parameters': parameters_by_iterations[iteration_id],
                'result_type': results[result_id],
                'verdict': verdicts.get(result_id, []),
                'has_error': test_result['has_error'],
            },
        )

    def iterations_grouper(iteration):
        return iteration['hash']

    def verdicts_grouper(result):
        result_type = result['result_type']
        verdict = ''
        if result['verdict']:
            verdict = '. '.join(map(str, result['verdict']))
        return f'{result_type}:{verdict}'

    # Group by iterations
    results_to_response = []
    data = sorted(results_list, key=iterations_grouper)
    for _, group_items in groupby(data, key=iterations_grouper):
        items = list(group_items)
        iteration = items[0]

        # Group by verdicts
        results_by_verdicts = defaultdict(list)
        data = sorted(items, key=verdicts_grouper)
        for verdict, results in groupby(data, key=verdicts_grouper):
            items = list(results)
            status = items[0]

            results_data = []
            for item in items:
                tags = ' '.join(tags_by_runs.get(item['run_id'], []))
                results_data.append(
                    {
                        'result_id': item['result_id'],
                        'date_and_tags': f"{item['start_date']} : {tags}",
                    },
                )

            results_by_verdicts[verdict] = {
                'obtained_result': {status['result_type']: status['verdict']},
                'results_data': results_data,
                'has_error': status['has_error'],
            }

        # Aggregate results, cast defaultdict to dict as Django template can't loop defaultdict
        results_to_response.append(
            {
                'hash': iteration['hash'],
                'parameters': {iteration['iteration_id']: iteration['parameters']},
                'results_by_verdicts': dict(results_by_verdicts),
            },
        )

    return results_to_response


def prepare_response(request, response_list, add_context=None):
    if add_context is None:
        add_context = {}
    page = int(request.GET.get('page', 1))
    paginator = Paginator(response_list, settings.ITEMS_PER_PAGE)
    page_range = [
        i for i in range(page - 3, page + 3 + 1) if i > 0 and i <= paginator.num_pages
    ]

    context = {
        'results_amount': len(response_list),
        'test_name': request.GET.get('test_name'),
        'page_range': page_range,
        'query_delimiter': settings.QUERY_DELIMITER,
        'param_delimiter': settings.KEY_VALUE_DELIMITER,
    }

    if add_context:
        context.update(add_context)

    return {
        'data': paginator.get_page(page),
        'context': context,
    }


def default_history_params(
    result_properties=('expected', 'unexpected'),
    session_properties=('notcompromised',),
    some_verdict='true',
    add_params=None,
    start=None,
    results=None,
):
    """
    This function returns default parameters for history request already encoded.
    To add parameters which are not among the default place them
    into 'add_params' as dict.
    The keys in 'add_params' as well as parameters for this function
    should be the same as in QueryString for the test_history page.

    Choose dates period to cover 3 months results based on the passed iteration
    start datetime or date (if exists) or calculate according.
    """

    if add_params is None:
        add_params = {}
    if results is None:
        results = ResultStatus.all_statuses()
    if isinstance(start, datetime):
        start = start.date()

    dates_period_needed = 3 * 30
    start_date, finish_date, _ = prepare_dates_period(delta_days=dates_period_needed)
    if start and start < finish_date:
        start_date = start
        finish_date = start_date + timedelta(days=dates_period_needed)

    params = {
        'start_date': start_date,
        'finish_date': finish_date,
        'results': settings.QUERY_DELIMITER.join(results),
        'result_properties': settings.QUERY_DELIMITER.join(result_properties),
        'session_properties': settings.QUERY_DELIMITER.join(session_properties),
        'some_verdict': some_verdict,
    }

    params.update(add_params)

    return urllib.parse.urlencode(params)
