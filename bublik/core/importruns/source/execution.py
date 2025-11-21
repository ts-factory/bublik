# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import Counter

from django.core.management import call_command
from django.db import transaction

from bublik.core.importruns import ImportMode, identify_run
from bublik.core.importruns.live.plan_tracking import PlanItem
from bublik.core.importruns.milog import EntryLevel, HandlerArtifacts
from bublik.core.importruns.utils import measure_time
from bublik.core.logging import get_task_or_server_logger
from bublik.core.run.objects import (
    add_expected_result,
    add_iteration,
    add_iteration_result,
    add_objective,
    add_obtained_result,
    add_requirements,
    add_tags,
    add_test,
    clear_run_count,
    del_blank_iteration_results,
    set_prologues_counts,
    set_run_count,
    set_run_import_mode,
)
from bublik.data.models import TestIterationResult


logger = get_task_or_server_logger()


@measure_time('handling iterations')
def handle_iterations(run_log, run, project_id, tests_nums_prologues):
    for iteration_data in run_log['iters']:
        handle_iteration(
            iteration_data,
            run,
            project_id,
            None,
            None,
            None,
            0,
            tests_nums_prologues,
        )


def handle_iteration(
    data,
    run,
    project_id,
    parent_iteration,
    parent_package,
    parent_test,
    parent_depth,
    tests_nums_prologues,
):
    handle_iteration.counter['iter_obj'] += 1

    if not data['name'] and data['type'] == 'session':
        data['name'] = 'session'

    test = add_test(data['name'], data['type'], parent_test)

    iteration = add_iteration(
        test,
        data['params'],
        data['hash'],
        parent_iteration,
        parent_depth,
    )
    handle_iteration.counter['created_iter_obj'] += add_iteration.counter['created']

    iteration_result = add_iteration_result(
        project_id,
        data['start_ts'],
        data['end_ts'],
        iteration,
        run,
        parent_package,
        data['tin'],
        data['test_id'],
    )

    add_objective(
        iteration_result,
        data['objective'],
    )

    add_requirements(
        iteration_result,
        data['reqs'],
    )

    obtained = data['obtained']

    plan_id = data['plan_id']
    if plan_id in tests_nums_prologues and obtained['result']['status'] not in [
        'PASSED',
        'FAKED',
    ]:
        set_prologues_counts(
            iteration_result,
            'expected_items_prologue',
            tests_nums_prologues[plan_id],
        )

    add_obtained_result(
        iteration_result,
        obtained['result']['status'],
        obtained['result'].get('verdicts'),
        data['err'],
    )

    expected = data.get('expected')
    if expected:
        keys = [expected['key']] if 'key' in expected else []
        notes = [expected['notes']] if 'notes' in expected else []
        for expected_result in expected['results']:
            if 'key' in expected_result:
                keys.append(expected_result['key'])
            if 'notes' in expected_result:
                notes.append(expected_result['notes'])
            add_expected_result(
                iteration_result,
                expected_result['status'],
                expected_result.get('verdicts'),
                obtained.get('tag_expression'),
                keys,
                notes,
            )
    else:
        add_expected_result(
            iteration_result,
            obtained['result']['status'],
            obtained['result'].get('verdicts'),
            obtained.get('tag_expression'),
            obtained['result'].get('key'),
            obtained['result'].get('notes'),
        )

    artifacts = obtained['result'].get('artifacts')
    if artifacts:
        HandlerArtifacts(iteration_result).handle_artifacts(artifacts)

    measurements = data.get('measurements')
    if measurements:
        HandlerArtifacts(iteration_result).handle_mi_artifacts(measurements)

    if not data['iters']:
        return
    for child_data in data['iters']:
        handle_iteration(
            child_data,
            run,
            project_id,
            iteration,
            iteration_result,
            test,
            parent_depth + 1,
            tests_nums_prologues,
        )


@measure_time('incremental import')
@transaction.atomic
def incremental_import(run_log, project_id, meta_data, run_completed, force):
    handle_iteration.counter = Counter(iter_obj=0, created_iter_obj=0)

    run_start = meta_data.run_start
    run_finish = meta_data.run_finish if run_completed else None

    force_update = False
    run_id = identify_run(meta_data.key_metas)

    if run_id:
        run = TestIterationResult.objects.get(id=run_id)
        was_online = run.import_mode == ImportMode.LIVE
        # It's always OK to add to imports that were originally online
        if run.finish and not was_online and not force:
            return run, False
        if run_completed:
            run.start = run_start
            run.finish = run_finish
            run.project_id = project_id
            run.save()
            force_update = True
            if was_online:
                logger.info('this is a reupload of an online import')
                del_blank_iteration_results(run_id)
            if force:
                logger.info('this is a re-import of an already imported run')
                del_blank_iteration_results(run_id)
            logger.info(f'the run will be fully added, finish timestamp - {run_finish}')
    else:
        run_data = {'test_run': None, 'start': run_start}
        if run_completed:
            run_data['finish'] = run_finish
            logger.info(f'the run will be fully added, finish timestamp - {run_finish}')
        else:
            force_update = True
            logger.info('the run will be partially added')

        run = TestIterationResult.objects.create(**run_data, project_id=project_id)

    set_run_import_mode(run, ImportMode.SOURCE)

    meta_data.handle(run, force_update)

    if not run_log:
        return run, True

    tests_nums_prologues = {}

    if run_log.get('plan'):
        plan_root = PlanItem(run_log['plan'])
        set_run_count(run, 'expected_items', plan_root.tests_num())
        plan_root.tests_num_prologue(tests_nums_prologues, plan_id=0)
    else:
        logger.warning('the execution plan is missing in MI log, or its version is unknown')
        clear_run_count(run, 'expected_items')

    if run_log.get('iters') is not None:
        handle_iterations(run_log, run, project_id, tests_nums_prologues)
        logger.info(
            f"the number of handled iterations is {handle_iteration.counter['iter_obj']}",
        )
        logger.info(
            'the number of created iteration objects is '
            f"{handle_iteration.counter['created_iter_obj']}",
        )
        logger.info(
            f'handling measurements during handling iterations took ['
            f'{HandlerArtifacts.handle_meas_time}]',
        )
        logger.info(f"the number of handled measurements is {EntryLevel.counter['meas_obj']}")
        logger.info(
            'the number of created measurement objects is '
            f"{EntryLevel.counter['created_meas_obj']}",
        )
        logger.info(
            'the number of created measurement result objects is '
            f"{EntryLevel.counter['created_meas_res_obj']}",
        )
    else:
        logger.info('there is no iterations in this run. Skip handling.')

    tags = run_log.get('tags', [])
    add_tags(run, tags)
    logger.info(f'the number of added tags is {len(tags)}')

    call_command('run_cache', 'delete', '-i', run.id, '--logger_out', True)

    return run, True
