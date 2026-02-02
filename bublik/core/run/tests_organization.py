# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.shortcuts import get_object_or_404

from bublik.core.queries import get_or_none
from bublik.data import models


def is_run_root(run_id):
    run = get_or_none(models.TestIterationResult.objects, id=run_id, test_run=None)
    return bool(run)


def get_run_root(result):
    """
    Param @result can be either TestIterationResult object or ID
    of a run itself or any test result.

    Returns run's root as a TestIterationResult object.
    """

    try:
        if not isinstance(result, models.TestIterationResult):
            result = get_object_or_404(models.TestIterationResult, id=result)

        if result.test_run is None:
            return result
        return result.test_run

    except Exception:
        # TODO: Should be writen to a runtime debug logger
        return None


def split_test_path(full_test_name):
    parts_iter = iter(full_test_name.strip('/').split('/'))
    segments = []

    for part in parts_iter:
        if part == '..':
            next_part = next(parts_iter, None)
            if next_part is not None:
                segments.append(f'../{next_part}')
        else:
            segments.append(part)

    return segments


def get_test_by_full_path(full_test_name):
    try:
        test_entity = models.ResultType.conv('test')
        package_entity = models.ResultType.conv('pkg')
        session_entity = models.ResultType.conv('session')

        test = None
        path_segments = split_test_path(full_test_name)
        for path_segment in path_segments[:-1]:
            test = models.Test.objects.get(
                name=path_segment,
                parent=test,
                result_type__in=[package_entity, session_entity],
            )

        # Try session as a part of the path several times in case of nesting session in session
        # as session is used in UI but it's not expected and can be absent in the full test path
        while test:
            parent = test
            test = get_or_none(
                models.Test.objects,
                name='session',
                parent=parent,
                result_type=session_entity,
            )

        return get_or_none(
            models.Test.objects,
            name=path_segments[-1],
            parent=parent,
            result_type=test_entity,
        )

    except Exception:
        return None


def get_test_ids_by_name(test_name):
    if test_name.startswith('../') or '/' not in test_name:
        # In some projects ../ is a valid part of a test name.
        test_entity = models.ResultType.conv('test')
        return list(
            models.Test.objects.filter(name=test_name, result_type=test_entity).values_list(
                'id',
                flat=True,
            ),
        )
    test = get_test_by_full_path(test_name)
    if not test:
        return []
    return [test.id]


def prepare_package_data(package, parents_str):
    data = []

    if package is None:
        descendants = models.Test.objects.filter(parent=None).order_by('name')
    else:
        descendants = package.get_descendants()

    if len(descendants) == 0:
        return None

    for i, descendant in enumerate(descendants):
        data.append({'text': descendant.name})
        descendant_data = prepare_package_data(descendant, parents_str + descendant.name + '/')
        if descendant_data is not None:
            data[i]['nodes'] = descendant_data
        else:
            history_link_prefix = '/history/test/?test_name='
            history_link = history_link_prefix + parents_str + descendant.name
            data[i]['href'] = history_link

    return data
