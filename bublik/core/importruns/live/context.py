# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import collections
from datetime import timedelta
import logging
from typing import ClassVar

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from bublik.core.datetime_formatting import utc_ts_to_dt
from bublik.core.importruns import ImportMode, identify_run
from bublik.core.importruns.live.plan_tracking import PlanItem, PlanTracker
from bublik.core.importruns.milog import HandlerArtifacts
from bublik.core.run.metadata import MetaData
from bublik.core.run.objects import (
    add_expected_result,
    add_iteration,
    add_iteration_result,
    add_obtained_result,
    add_tags,
    add_test,
    set_run_count,
    set_run_import_mode,
    set_run_status,
)
from bublik.data.models.result import (
    ResultType,
    Test,
    TestIteration,
    TestIterationResult,
)


logger = logging.getLogger('bublik.server')


class LiveLogError(Exception):
    '''Exceptions related to live log import.'''

    def __init__(self, msg, **kwargs):
        self.data = kwargs
        self.data['message'] = msg

    def to_response(self):
        return Response(
            status=self.STATUS,
            data=self.data,
        )


class LLInvalidInputError(LiveLogError):
    '''User-supplied data is invalid.'''

    STATUS = status.HTTP_400_BAD_REQUEST


class LLInternalError(LiveLogError):
    '''An internal error has occured during request processing.'''

    STATUS = status.HTTP_500_INTERNAL_SERVER_ERROR


class LLNotImplementedError(LiveLogError):
    '''An internal error has occured during request processing.'''

    STATUS = status.HTTP_501_NOT_IMPLEMENTED


class NodeIDConverter:
    '''
    Track node ID to TestIterationResult ID mapping for easy artifact
    message processing.
    '''

    HISTORY_SIZE = 20

    def __init__(self):
        self.items = []

    def add_rule(self, node_id, result_id):
        self.items.append((node_id, result_id))
        if len(self.items) > self.HISTORY_SIZE:
            self.items.pop(0)

    def get_result_id(self, target_node_id):
        for node_id, result_id in self.items:
            if node_id == target_node_id:
                return result_id
        return None


class TestStackItem:
    '''Description of the currently running entity in Bublik terms.'''

    def __init__(
        self,
        test_stack_type=None,
        seqno=None,
        node_id=None,
        plan_id=None,
        test=None,
        iteration=None,
        result=None,
    ):
        self.test_stack_type = test_stack_type
        self.seqno = seqno
        self.node_id = node_id
        self.plan_id = plan_id
        self.test = test
        self.iteration = iteration
        self.result = result


class LiveLogContext:
    '''
    Context that should be preserved between the heartbeats of TE
    log streaming protocol.
    '''

    # Additional time to allow a request to reach Bublik
    # (and let Bublik process other tasks)
    TRIP_TIME = 60
    # Node ID assigned to test items that were lost
    LOST_ITEM_NODE_ID = -1

    REQUIRED_EVENT_DATA: ClassVar[dict[str, list[str]]] = {
        'test_start': ['id', 'parent', 'plan_id', 'ts', 'node_type', 'name'],
        'test_end': ['id', 'plan_id', 'ts'],
    }

    @staticmethod
    def check_event_data(event_name, event):
        missing = []
        for attr in LiveLogContext.REQUIRED_EVENT_DATA[event_name]:
            if event.get(attr) is None:
                missing.append(attr)
        if missing:
            msg = f'{event_name} event is missing items'
            raise LLInvalidInputError(msg, items=missing)

        if event.get('plan_id', -1) == -1:
            msg = 'processing events without plan IDs has not been implemented'
            raise LLNotImplementedError(
                msg,
            )

    def __init__(self, data):
        self.run = None

        meta_data = None
        tags = None

        if data.get('tags') is not None:
            tags = {}
            for tag in data['tags']:
                if 'name' not in tag:
                    msg = 'all tags should have a name'
                    raise LLInvalidInputError(msg)
                tags[tag['name']] = tag.get('value')

        if 'interval' not in data:
            msg = 'heartbeat interval is required'
            raise LLInvalidInputError(msg)
        self.heartbeat = data['interval'] + self.TRIP_TIME

        if 'meta_data' in data:
            try:
                for meta in data['meta_data']['metas']:
                    if meta.get('name', '') == 'START_TIMESTAMP':
                        break
                else:
                    if settings.DEBUG and 'ts' in data:
                        data['meta_data']['metas'].append(
                            {
                                'name': 'START_TIMESTAMP',
                                'type': 'timestamp',
                                'value': utc_ts_to_dt(data['ts']).isoformat(),
                            },
                        )
                    else:
                        msg = 'start time not specified'
                        raise LLInvalidInputError(msg)
                meta_data = MetaData(data['meta_data'])
                self.run = identify_run(meta_data.key_metas)
                self.last_ts = meta_data.run_start
                self.project = meta_data.project
            except Exception as e:
                err = e if isinstance(e, str) else 'error occurred while processing metadata'
                raise LLInternalError(err) from err
        else:
            msg = 'meta_data is required'
            raise LLInvalidInputError(msg)

        force_update = True
        if self.run is None:
            run_data = {
                'test_run': None,
                'start': meta_data.run_start,
                'project_id': self.project.id,
            }
            self.run = TestIterationResult.objects.create(**run_data)
            force_update = False
        else:
            msg = f'this run already exists ({self.run})'
            raise LLInvalidInputError(msg)

        if not meta_data.handle(self.run, force_update):
            msg = 'failed to update metadata'
            raise LLInternalError(msg)

        if tags is not None:
            add_tags(self.run, tags)

        if data.get('plan') is not None:
            plan_root = PlanItem(data['plan'])
            self.plan_tracker = PlanTracker(plan_root)
        else:
            msg = 'no execution plan provided'
            raise LLInvalidInputError(msg)

        set_run_count(self.run, 'expected_items', plan_root.tests_num())
        set_run_import_mode(self.run, ImportMode.LIVE)

        # Set run status if it hasn't been specified in meta data
        if meta_data.status_meta is None:
            set_run_status(self.run, 'RUN_STATUS_RUNNING')

        self.test_stack = collections.deque()
        self.current_seqno = 1
        self.max_node_id = 0
        self.node_conv = NodeIDConverter()

    def serialize(self):
        '''Prepare the stack to be cached, deflate model objects.'''
        if isinstance(self.run, int):
            return

        self.run = self.run.id
        for item in self.test_stack:
            item.test = item.test.id
            item.iteration = item.iteration.id
            item.result = item.result.id

    def deserialize(self):
        '''Inflate model objects after the stack was extracted from cache.'''
        if isinstance(self.run, TestIterationResult):
            return

        self.run = TestIterationResult.objects.get(id=self.run)
        for item in self.test_stack:
            item.test = Test.objects.get(id=item.test)
            item.iteration = TestIteration.objects.get(id=item.iteration)
            item.result = TestIterationResult.objects.get(id=item.result)

    def executing_test(self):
        return self.test_stack and self.test_stack[-1].test_stack_type == ResultType.TEST

    def executing_something(self):
        return bool(self.test_stack)

    def top_node_id(self):
        if self.test_stack:
            return self.test_stack[-1].node_id
        return 0

    def push_stack_item(self, **kwargs):
        self.test_stack.append(TestStackItem(**kwargs, seqno=self.current_seqno))
        self.current_seqno += 1

    def pop_stack_item(self):
        self.test_stack.pop()

    def peek_stack(self):
        if not self.executing_something():
            return None
        return self.test_stack[-1]

    def ts_microstep(self):
        '''
        Increase the last timestamp by a microsecond.

        This function is used to ensure that all LOST iteration results have
        unique timestamps.
        '''
        self.last_ts += timedelta(microseconds=1)
        return self.last_ts

    def get_expected_event(self):
        plan_event = self.plan_tracker.peek_event()
        if plan_event is None:
            msg = 'execution finished, yet more events have been received'
            raise LLInvalidInputError(msg)
        return plan_event

    def get_parent(self):
        current_top = self.peek_stack()
        if current_top is None:
            return None, None, None
        return (current_top.test, current_top.iteration, current_top.result)

    def finished_result(self, result):
        result.finish = self.last_ts
        result.save()
        return result

    def finish_run(self, status_key):
        self.run.finish = self.last_ts
        self.run.save()
        set_run_status(self.run, status_key)

        # To avoid cyclic dependency between run.actions and this module
        from bublik.core.run.actions import prepare_cache_for_completed_run

        prepare_cache_for_completed_run(self.run)

    @staticmethod
    def param_list_to_dict(param_list):
        return dict(param_list or [])

    def plan_skip_handlers(self):
        """
        Prepare plan item handlers to skip items.

        The gap will be filled with blank test iterations (i.e. without any
        arguments) and test iteration results with status 'LOST'.
        """

        # mirror the actions on self.test_stack
        def on_item_enter(plan_item):
            assert not self.executing_test()
            self.ts_microstep()
            # Create Test
            parent_test, parent_iter, parent_result = self.get_parent()
            test = add_test(plan_item.name, plan_item.node_type, parent_test)
            # Create TestIteration
            test_iteration = add_iteration(test, None, '', parent_iter, len(self.test_stack))
            # Create TestIterationResult
            test_iteration_result = add_iteration_result(
                project_id=self.project.id,
                start_time=self.last_ts,
                iteration=test_iteration,
                run=self.run,
                parent_package=parent_result,
                tin=-2,
                exec_seqno=self.current_seqno,
            )
            self.push_stack_item(
                test_stack_type=plan_item.node_type,
                node_id=self.LOST_ITEM_NODE_ID,
                plan_id=plan_item.id,
                test=test,
                iteration=test_iteration,
                result=test_iteration_result,
            )

        def on_item_exit(plan_item):
            assert self.executing_something()
            self.ts_microstep()
            result = self.finished_result(self.peek_stack().result)
            add_obtained_result(result, 'LOST', err='Data was lost during live import')
            self.test_stack.pop()

        return on_item_enter, on_item_exit

    def advance_plan(self, target_plan_id, stop_at_start, target_dt):
        '''
        Fast-forward test execution state to the point where target_plan_id
        is the plan id of the next expected test_start event (or test_end,
        depending on the value of the stop_at_start parameter).
        '''

        on_item_enter, on_item_exit = self.plan_skip_handlers()
        if on_item_enter is not None and on_item_exit is not None:
            self.plan_tracker.skip_until(
                target_plan_id,
                stop_at_start,
                on_item_enter,
                on_item_exit,
            )
            # We will discard the results anyway
            event = self.plan_tracker.peek_event()
            if not stop_at_start and event is not None:
                item, enter = event
                assert not enter
                on_item_exit(item)
                self.plan_tracker.next_event()

    def fatal_error(self):
        '''Finish the import after a fatal error.'''
        # Finish everything that's currently running
        for item in reversed(self.test_stack):
            self.ts_microstep()
            result = self.finished_result(item.result)
            add_obtained_result(result, 'LOST')
        self.test_stack = collections.deque()

        # Set the finish time for the whole run
        self.ts_microstep()
        self.finish_run('RUN_STATUS_ERROR')

    def handle_test_start(self, event):
        """Process 'test_start' events produced by TE."""
        self.check_event_data('test_start', event)
        node_id = event['id']
        plan_id = event['plan_id']
        parent_id = event['parent']
        name = event.get('name')
        node_type = event.get('node_type')
        ts = utc_ts_to_dt(event['ts'])
        test_params = self.param_list_to_dict(event.get('params'))
        test_hash = event.get('hash', '')
        test_tin = event.get('tin', -1)

        logger.debug(f'handing test start {event}')

        plan_item, started = self.get_expected_event()
        if not started or self.executing_test() or node_id > self.max_node_id + 1:
            logger.warning('Some test control events were lost')
            logger.debug(
                f'node_id {node_id} max_node_id {self.max_node_id}, '
                f'plan_id {plan_id}, exp_plan {plan_item}',
            )
            self.advance_plan(plan_id, True, ts)
            plan_item, started = self.get_expected_event()
            logger.debug(
                f'node_id {node_id} max_node_id {self.max_node_id}, '
                f'plan_id {plan_id}, exp_plan {plan_item}',
            )
        self.max_node_id = node_id

        if plan_item.id < plan_id:
            self.plan_tracker.skip_until(plan_id, True)

        assert not self.executing_test()

        plan_item, started = self.get_expected_event()
        assert started
        if plan_item.name != name or plan_item.node_type != node_type:
            msg = f'expected {plan_item}, got {node_type} {name}'
            raise LLInvalidInputError(msg)

        # Can't know anything about lost items, so allow that
        if self.top_node_id() not in (self.LOST_ITEM_NODE_ID, parent_id):
            msg = f'node {node_id}: expected parent {self.top_node_id()}, got {parent_id}'
            raise LLInvalidInputError(
                msg,
            )

        assert plan_id == plan_item.id, f'plan_id {plan_id}, expected {plan_item.id}'

        logger.debug(f'setting up result for {plan_item}')
        self.last_ts = ts

        # Find parent Test and TestIterationResult
        parent_test, parent_iter, parent_result = self.get_parent()

        # Add Test
        logger.debug(f'adding test {name}')
        test = add_test(name, node_type, parent_test)

        # Add TestIteration
        logger.debug('adding iteration')
        test_iteration = add_iteration(
            test,
            test_params,
            test_hash,
            parent_iter,
            len(self.test_stack),
        )

        # Add TestIterationResult
        logger.debug('adding result')
        test_iteration_result = add_iteration_result(
            project_id=self.project.id,
            start_time=self.last_ts,
            iteration=test_iteration,
            run=self.run,
            parent_package=parent_result,
            tin=test_tin,
            exec_seqno=self.current_seqno,
        )

        self.push_stack_item(
            test_stack_type=node_type,
            node_id=node_id,
            plan_id=plan_id,
            test=test,
            iteration=test_iteration,
            result=test_iteration_result,
        )
        self.node_conv.add_rule(node_id, test_iteration_result.id)
        self.plan_tracker.next_event()

    def handle_test_end(self, event):
        """Process 'test_end' events produced by TE."""
        self.check_event_data('test_end', event)
        node_id = event['id']
        plan_id = event['plan_id']
        ts = utc_ts_to_dt(event['ts'])
        error = event.get('error')
        tags_expr = event.get('tags_expr')
        expected_results = event.get('expected')

        logger.debug(f'handing test end {event}')

        self.max_node_id = max(self.max_node_id, node_id)
        if not self.executing_something() or plan_id != self.test_stack[-1].plan_id:
            logger.warning('Some test control events were lost (end)')
            plan_item, started = self.get_expected_event()
            logger.debug(
                f'node_id {node_id} max_node_id {self.max_node_id}, '
                f'plan_id {plan_id}, exp_plan {plan_item}',
            )
            # Drop this result: we don't know where it comes from, so it's meaningless
            self.advance_plan(plan_id, False, ts)
            plan_item, started = self.get_expected_event()
            logger.debug(
                f'node_id {node_id} max_node_id {self.max_node_id}, '
                f'plan_id {plan_id}, exp_plan {plan_item}',
            )
            return

        plan_item, started = self.get_expected_event()
        if started:
            self.plan_tracker.skip_until(plan_id, False)

        plan_item, started = self.get_expected_event()
        assert not started, f'expected end event, got start event for {plan_item}'

        assert self.executing_something()
        current_item = self.peek_stack()
        assert current_item.node_id in (self.LOST_ITEM_NODE_ID, node_id)
        assert current_item.plan_id == plan_id

        self.last_ts = ts

        logger.debug(f'adding result for {plan_item}')

        # Set TestIterationResult finish time
        test_iteration_result = self.finished_result(current_item.result)

        # Bublik only cares about test (not session or package) results
        if self.executing_test():
            # Register obtained result
            obtained = event['obtained']
            add_obtained_result(
                test_iteration_result,
                obtained['status'],
                obtained.get('verdicts'),
                error,
            )

            # Register expected results
            if expected_results is None:
                if error is None:
                    # No error and no explicitly mentioned expectations.
                    # This means the obtained result was expected.
                    add_expected_result(
                        test_iteration_result,
                        obtained['status'],
                        obtained.get('verdicts'),
                        tags_expr,
                        obtained.get('key'),
                        obtained.get('notes'),
                    )
            else:
                for expected in expected_results:
                    add_expected_result(
                        test_iteration_result,
                        expected['status'],
                        expected.get('verdicts'),
                        tags_expr,
                        expected.get('key'),
                        expected.get('notes'),
                    )

        self.pop_stack_item()
        self.plan_tracker.next_event()

    def feed(self, events):
        '''Process events produced by TE.'''

        for event in events:
            if 'type' not in event:
                msg = 'event does not have a type'
                raise LLInvalidInputError(msg)
            if event['type'] == 'test_start':
                self.handle_test_start(event)
            elif event['type'] == 'test_end':
                self.handle_test_end(event)
            elif event['type'] == 'artifact':
                # Get TestIterationResult associated with the artifact
                test_iteration_result = TestIterationResult.objects.get(
                    id=self.node_conv.get_result_id(event['test_id']),
                )

                if test_iteration_result:
                    artifact_handler = HandlerArtifacts(test_iteration_result)
                    artifact_handler.handle([event.get('body')])
                else:
                    logger.error(
                        'artifact processing failure: missing test iteration result',
                    )

    def finish(self, data):
        '''Initialize import using TE log streaming protocol.'''
        if 'ts' not in data:
            msg = 'timestamp is required'
            raise LLInvalidInputError(msg)
        dt = utc_ts_to_dt(data['ts'])
        self.last_ts = dt
        self.finish_run('RUN_STATUS_DONE')
