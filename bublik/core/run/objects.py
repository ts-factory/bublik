# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import Counter
import logging

from django.core.exceptions import MultipleObjectsReturned, ObjectDoesNotExist
from django.db.models import Count

from bublik.core.config.services import ConfigServices
from bublik.core.meta.categorization import categorize_meta
from bublik.core.run.keys import prepare_expected_key
from bublik.core.run.utils import prepare_date
from bublik.core.shortcuts import serialize
from bublik.data.models import (
    GlobalConfigs,
    MetaResult,
    Reference,
    ResultType,
    RunStatus,
    Test,
    TestIteration,
    TestIterationRelation,
    TestIterationResult,
)
from bublik.data.serializers import (
    ExpectationSerializer,
    MetaSerializer,
    TestArgumentSerializer,
)


logger = logging.getLogger('bublik.server')


def add_test(test_name, result_type, parent_test):
    test, _ = Test.objects.get_or_create(
        name=test_name,
        parent=parent_test,
        result_type=ResultType.conv(result_type),
    )
    return test


def add_relation(iteration, parent_iteration, depth):
    relation, _ = TestIterationRelation.objects.get_or_create(
        test_iteration=iteration,
        parent_iteration=parent_iteration,
        depth=depth,
    )
    return relation


def add_iteration(test, iteration_params, iteration_hash, parent_iteration, parent_depth):
    add_iteration.counter = Counter(created=0)

    def process_test():
        iteration, created = TestIteration.objects.get_or_create(test=test, hash=iteration_hash)
        if created:
            add_iteration.counter['created'] += 1
            if iteration_params:
                for n, v in iteration_params.items():
                    arg_serializer = serialize(
                        TestArgumentSerializer,
                        {'name': n, 'value': v},
                        logger,
                    )
                    arg, _ = arg_serializer.get_or_create()
                    iteration.test_arguments.add(arg)
        return iteration

    def process_session_pkg():
        iteration, created = TestIteration.objects.get_or_create(test=test, hash=None)
        if created:
            add_iteration.counter['created'] += 1
        return iteration

    handlers = {
        ResultType.TEST: process_test,
        ResultType.SESSION: process_session_pkg,
        ResultType.PACKAGE: process_session_pkg,
    }

    handler = handlers.get(ResultType.inv(test.result_type))
    if not handler:
        logger.error(f'unknown entity type: {test.result_type}, ignoring it')

    iteration = handler()

    if parent_depth == 0:
        add_relation(iteration, parent_iteration, parent_depth)
    else:
        tmp_parent = parent_iteration
        tmp_cur_depth = 1

        while tmp_cur_depth <= parent_depth:
            add_relation(iteration, tmp_parent, tmp_cur_depth)

            if tmp_cur_depth == parent_depth:
                break

            tmp_relation = TestIterationRelation.objects.get(test_iteration=tmp_parent, depth=1)
            tmp_parent = tmp_relation.parent_iteration
            tmp_cur_depth += 1

    return iteration


def add_iteration_result(
    project_id,
    start_time,
    finish_time=None,
    iteration=None,
    run=None,
    parent_package=None,
    tin=None,
    exec_seqno=None,
):

    try:
        # get objects by passed run and exec_seqno
        iteration_result = TestIterationResult.objects.get(
            exec_seqno=exec_seqno,
            test_run=run,
        )
        # check the objects for compliance (there may be a corresponding blank object
        # with a different special tin (-1 or -2) and iteration)
        if iteration_result.parent_package != parent_package or (
            iteration_result.tin != int(tin) and iteration_result.tin > -1
        ):
            msg = (
                f'TestIterationResult object with passed exec_seqno ({exec_seqno}) '
                f'already exists to current run: {iteration_result}'
            )
            raise ValueError(msg)
        iteration_result.project_id = project_id
        iteration_result.start = prepare_date(start_time)
        iteration_result.finish = prepare_date(finish_time) if finish_time else None
        iteration_result.tin = tin
        iteration_result.iteration = iteration
        iteration_result.save()
    except MultipleObjectsReturned as mor:
        iteration_result = TestIterationResult.objects.filter(
            exec_seqno=exec_seqno,
            test_run=run,
        )
        msg = (
            f'duplicated TestIterationResult objects were found! '
            f'IDs: {list(iteration_result.values_list("id", flat=True))}. '
            'Check and clean DB!'
        )
        raise ValueError(msg) from mor
    except ObjectDoesNotExist:
        iteration_result = TestIterationResult.objects.create(
            test_run=run,
            iteration=iteration,
            parent_package=parent_package,
            exec_seqno=exec_seqno,
            tin=tin,
            start=prepare_date(start_time),
            finish=prepare_date(finish_time) if finish_time else None,
            project_id=project_id,
        )

    return iteration_result


def add_meta_result(m_data, mr_data):
    meta_serializer = serialize(MetaSerializer, m_data, logger)
    meta, created = meta_serializer.get_or_create()
    if created:
        categorize_meta(meta)
    MetaResult.objects.get_or_create(meta=meta, **mr_data)


def update_or_create_meta_result(m_data, mr_data):
    meta_head = {f'meta__{k}': m_data[k] for k in {'name', 'type'} & m_data.keys()}

    meta_serializer = serialize(MetaSerializer, m_data, logger)
    meta, created = meta_serializer.get_or_create()
    if created:
        categorize_meta(meta)
    MetaResult.objects.update_or_create(**mr_data, **meta_head, defaults={'meta': meta})


def clear_meta_result(m_data, mr_data):
    meta_serializer = serialize(MetaSerializer, m_data, logger)
    meta, _ = meta_serializer.get_or_create()
    MetaResult.objects.filter(meta=meta, **mr_data).delete()


def add_run_log(run, source_suffix, logs_base):
    reference, _ = Reference.objects.get_or_create(
        uri=logs_base['uri'][-1],
        name=logs_base['name'],
    )
    add_meta_result(
        m_data={'type': 'log', 'value': source_suffix},
        mr_data={'result': run, 'reference': reference},
    )


def add_tags(run, tags):
    if not tags:
        return

    for tag_name, tag_value in tags.items():
        add_meta_result(
            m_data={'name': tag_name, 'type': 'tag', 'value': tag_value},
            mr_data={'result': run},
        )


def add_import_id(run, import_id):
    add_meta_result(
        m_data={'name': 'import_id', 'type': 'import', 'value': import_id},
        mr_data={'result': run},
    )


def set_run_count(run, count_name, count_value):
    update_or_create_meta_result(
        m_data={'name': count_name, 'type': 'count', 'value': str(count_value)},
        mr_data={'result': run},
    )


def set_prologues_counts(iteration, count_name, count_value):
    update_or_create_meta_result(
        m_data={'name': count_name, 'type': 'count', 'value': str(count_value)},
        mr_data={'result': iteration},
    )


def clear_run_count(run, count_name):
    clear_meta_result(m_data={'name': count_name, 'type': 'count'}, mr_data={'result': run})


def set_run_import_mode(run, import_mode):
    update_or_create_meta_result(
        m_data={'name': 'import_mode', 'type': 'import', 'value': import_mode},
        mr_data={'result': run},
    )


def run_status_default(status_key):
    default_status = {
        'RUN_STATUS_RUNNING': RunStatus.RUNNING,
        'RUN_STATUS_DONE': RunStatus.DONE,
        'RUN_STATUS_ERROR': RunStatus.ERROR,
        'RUN_STATUS_WARNING': RunStatus.WARNING,
        'RUN_STATUS_STOPPED': RunStatus.STOPPED,
        'RUN_STATUS_BUSY': RunStatus.BUSY,
        'RUN_STATUS_INTERRUPTED': RunStatus.INTERRUPTED,
    }
    return default_status[status_key]


def set_run_status(run, status_key):
    project_id = run.project.id
    status_meta_name = ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        'RUN_STATUS_META',
        project_id,
    )
    status_value = ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        status_key,
        project_id,
        default=run_status_default(status_key),
    )
    if status_meta_name:
        update_or_create_meta_result(
            m_data={
                'name': status_meta_name,
                'type': 'label',
                'value': status_value,
            },
            mr_data={'result': run},
        )
    else:
        logger.error('cannot set run status because RUN_STATUS_META is not set')


def add_objective(iteration_result, objective):
    add_meta_result(
        m_data={'type': 'objective', 'value': objective},
        mr_data={'result': iteration_result},
    )


def add_requirements(iteration_result, requirements):
    for requirement in requirements:
        add_meta_result(
            m_data={'type': 'requirement', 'value': requirement},
            mr_data={'result': iteration_result},
        )


def add_obtained_result(iteration_result, result, verdicts=None, err=None):
    if verdicts is not None:
        for serial, verdict in enumerate(verdicts):
            add_meta_result(
                m_data={'type': 'verdict', 'value': verdict},
                mr_data={'result': iteration_result, 'serial': serial},
            )

    if result is not None:
        add_meta_result(
            m_data={'type': 'result', 'value': result},
            mr_data={'result': iteration_result},
        )

    if err:
        add_meta_result(
            m_data={'type': 'err', 'value': err},
            mr_data={'result': iteration_result},
        )


def add_expected_result(
    iteration_result,
    result,
    verdicts=None,
    tag_expression=None,
    keys=None,
    notes=None,
):
    expect_metas = []

    if result is not None:
        expect_metas.append({'meta': {'type': 'result', 'value': result}})

    if verdicts is not None:
        for index, verdict in enumerate(verdicts):
            expect_metas.append(
                {'meta': {'type': 'verdict_expected', 'value': verdict}, 'serial': index},
            )

    if tag_expression is not None:
        expect_metas.append({'meta': {'type': 'tag_expression', 'value': tag_expression}})

    if keys:
        for key in keys:
            run = iteration_result.test_run if iteration_result.test_run else iteration_result
            project_id = run.project.id
            expect_metas.extend(
                prepare_expected_key(
                    key,
                    project_id,
                ),
            )

    if notes:
        for index, note in enumerate(notes):
            expect_metas.append({'meta': {'type': 'note', 'value': note}, 'serial': index})

    expectation_serializer = serialize(
        ExpectationSerializer,
        {'expectmeta_set': expect_metas},
        logger,
    )
    expectation, _ = expectation_serializer.get_or_create()
    expectation.results.add(iteration_result)

    return expectation


def del_blank_iteration_results(run_id):
    run_tir = TestIterationResult.objects.filter(
        test_run__id=run_id,
    )

    run_tir_es_dup_counts = (
        run_tir.values('exec_seqno')
        .annotate(es_count=Count('exec_seqno'))
        .filter(es_count__gt=1)
    )

    run_tir_dups = run_tir.filter(
        exec_seqno__in=[tir['exec_seqno'] for tir in run_tir_es_dup_counts],
    )
    blank_iter_res = run_tir_dups.filter(iteration__hash='')

    logger.info(f'there are {len(blank_iter_res)} blank test iteration results in the DB')

    # protection against cascading deletion of other test iteration results
    blank_iter_res_ids = list(blank_iter_res.values_list('id', flat=True))
    blank_iter_res_children = run_tir.filter(
        parent_package__in=blank_iter_res_ids,
        test_run_id__in=blank_iter_res_ids,
    )
    if blank_iter_res_children:
        logger.warning(
            'some blank test iteration results have children! '
            'Data may be lost as a result of deletion, deletion is skipped',
        )
    else:
        logger.info('blank test iteration results have been successfully deleted!')
        blank_iter_res.delete()
