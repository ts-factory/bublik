# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime, timedelta
import hashlib

from itertools import groupby
import urllib

from deepdiff import DeepHash
from django.conf import settings

from bublik.core.datetime_formatting import display_to_milliseconds, get_duration
from bublik.core.run.stats import get_expected_results
from bublik.core.run.utils import prepare_dates_period
from bublik.core.utils import key_value_dict_transforming
from bublik.data.models import ResultStatus, TestIterationResult


def generate_hashkey(request):
    hashkey = hashlib.md5()

    for key, value in request.GET.items():
        if any(key == k for k in ('page', 'clicksearch', 'subsearch')) or not value:
            continue
        hashkey.update(f'{key}={value!s}%'.encode())

    return hashkey.hexdigest()


def prepare_list_results(
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
        result_start = test_result['start']
        result_finish = test_result['finish']

        # Handle expected result
        expected_result_data = {}
        test_result_obj = TestIterationResult.objects.get(id=result_id)
        expected_results = get_expected_results(test_result_obj)
        if expected_results:
            expected_result = expected_results[0]
            expected_result_data = {
                'result_type': expected_result['result'],
                'verdict': expected_result['verdicts'],
            }

        # Handle obtained result
        obtained_result_data = {
            'result_type': results[result_id],
            'verdict': verdicts.get(result_id, []),
        }

        # Handle parameters
        parameters_list = key_value_dict_transforming(parameters_by_iterations[iteration_id])

        # Handle metadata
        metadata = metadata_by_runs.get(run_id, [])

        result = {
            'start_date': display_to_milliseconds(result_start),
            'finish_date': display_to_milliseconds(result_finish),
            'duration': get_duration(result_start, result_finish),
            'obtained_result': obtained_result_data,
            'expected_result': expected_result_data,
            'important_tags': important_tags.get(run_id, []),
            'relevant_tags': relevant_tags.get(run_id, []),
            'metadata': metadata,
            'parameters': parameters_list,
            'has_error': test_result['has_error'],
            'has_measurements': test_result['is_measurements'],
            'run_id': run_id,
            'result_id': result_id,
            'iteration_id': iteration_id,
        }

        results_to_response.append(result)

    return results_to_response


def group_results_by_iteration(test_results):
    def iterations_grouper(iteration):
        return iteration['iteration_hash']

    data = sorted(test_results, key=iterations_grouper)
    return groupby(data, key=iterations_grouper)


def group_results_by_verdict(test_results, important_tags, relevant_tags):
    def verdicts_grouper(result):
        data = {key: result[key] for key in ('result_type', 'verdict')}
        return DeepHash(data)[data]

    # Group by verdicts
    results_by_verdicts = []
    data = sorted(test_results, key=verdicts_grouper)
    for verdict_hash, result_groups in groupby(data, key=verdicts_grouper):
        results = list(result_groups)
        result_status = results[0]

        results_data = []
        for result in results:
            run_id = result['run_id']

            results_data.append(
                {
                    'run_id': run_id,
                    'result_id': result['result_id'],
                    'start_date': result['start_date'],
                    'important_tags': important_tags.get(run_id, []),
                    'relevant_tags': relevant_tags.get(run_id, []),
                },
            )

        results_by_verdicts.append(
            {
                'key': verdict_hash,
                'result_type': result_status['result_type'],
                'has_error': result_status['has_error'],
                'verdict': result_status['verdict'],
                'results_data': results_data,
            },
        )

    return results_by_verdicts


def group_results(
    test_results_by_iteration,
    important_tags,
    relevant_tags,
    parameters_by_iterations,
    results,
    verdicts,
):

    # Preare data for iteration group
    results_to_response = []
    for test_results in test_results_by_iteration:
        group = test_results[0]
        iteration_id = group['iteration_id']

        # Handle parameters
        parameters_list = key_value_dict_transforming(parameters_by_iterations[iteration_id])

        iteration_group = {
            'hash': group['iteration_hash'],
            'iteration_id': iteration_id,
            'parameters': parameters_list,
            'results_by_verdicts': {},
        }

        results_list = []
        # Prepare list results data for the iteration group
        for test_result in test_results:
            run_id = test_result['run_id']
            result_id = test_result['id']
            results_list.append(
                {
                    'run_id': run_id,
                    'result_id': result_id,
                    'start_date': test_result['start'].date(),
                    'result_type': results[result_id],
                    'verdict': verdicts.get(result_id, []),
                    'has_error': test_result['has_error'],
                },
            )

        results_by_verdicts = group_results_by_verdict(
            results_list,
            important_tags,
            relevant_tags,
        )
        iteration_group['results_by_verdicts'] = results_by_verdicts
        results_to_response.append(iteration_group)

    return results_to_response


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
