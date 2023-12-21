# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from bublik.data.models.result import ResultType


class PlanItem:
    '''
    Description of an execution plan item.

    While this class contains enough information to traverse the whole
    execution plan, using a PlanTracker is advised.
    '''

    def __init__(self, item, parent=None):
        self.id = 0
        self.name = item['name']
        self.node_type = item['type']
        self.parent = parent
        self.has_prologue = False
        self.has_epilogue = False
        self.children = []

        if 'prologue' in item:
            self.children.append(PlanItem(item['prologue'], self))
            self.has_prologue = True

        if 'children' in item:
            keepalive = None
            if 'keepalive' in item:
                keepalive = PlanItem(item['keepalive'], self)
            for child in item['children']:
                iterations = child.get('iterations', 1)
                child_item = PlanItem(child, self)
                for _ in range(iterations):
                    if keepalive is not None:
                        self.children.append(keepalive)
                    if child['type'] != 'skipped':
                        self.children.append(child_item)

        if 'epilogue' in item:
            self.children.append(PlanItem(item['epilogue'], self))
            self.has_epilogue = True

    def __str__(self):
        return f'{self.node_type} {self.name} (pid {self.id})'

    __repr__ = __str__

    def tests_num(self):
        if self.node_type == ResultType.TEST:
            return 1

        tests_num = 0
        for child in self.children:
            tests_num += child.tests_num()
        return tests_num

    def tree_nodes_num(self):
        '''
        This function returns the number of nodes in the tree, whose root is
        the current node (self).
        '''
        if not self.children:
            return 1

        # set 1 to take current node into account
        tree_nodes_num = 1
        for child in self.children:
            tree_nodes_num += child.tree_nodes_num()
        return tree_nodes_num

    def tests_num_prologue(self, tests_nums_prologues, plan_id):
        '''
        This function returns a dictionary whose keys are the 'plan_id' of the prologues,
        and the values are the number of tests that will be skipped if the prologue
        is skipped or failed.
        '''
        if self.has_prologue:
            prologue_tests_num = 0
            for child in self.children:
                prologue_tests_num += child.tests_num()
            # prologues and epilogues are executed in any case, so they do not need to be
            # included in the counter
            prologue_tests_num -= 1
            if self.has_epilogue:
                prologue_tests_num -= 1
            # current plan_id corresponds to pkg/session, and we have to link the counter to the
            # prologue test
            tests_nums_prologues[plan_id + 1] = prologue_tests_num

        # add 1 to switch to the next node
        plan_id += 1
        for child in self.children:
            child.tests_num_prologue(tests_nums_prologues, plan_id)
            plan_id += child.tree_nodes_num()

        return tests_nums_prologues


class PlanStackItem:
    '''
    This class contains aux data required to traverse the execution plan.
    '''

    def __init__(self, plan_item):
        self.item = plan_item
        self.current_child = -1
        self.running = True

    def next_child(self):
        if self.current_child == len(self.item.children) - 1:
            return None
        self.current_child += 1
        return PlanStackItem(self.item.children[self.current_child])


class PlanTracker:
    '''High-level interface to tracking test execution.'''

    def do_nothing(self, x):
        return None

    def __init__(self, root_item):
        self.stack = [PlanStackItem(root_item)]
        self.next_id = 1

    def next_event(self):
        '''Advance one step through the plan.'''
        if len(self.stack) == 0:
            return

        if not self.stack[-1].running:
            self.stack.pop()
            if len(self.stack) == 0:
                return

        child = self.stack[-1].next_child()
        if child is None:
            self.stack[-1].running = False
        else:
            child.item.id = self.next_id
            self.next_id += 1
            self.stack.append(child)

    def peek_event(self):
        '''Return the next event, do not advance.'''
        if len(self.stack) == 0:
            return None

        return self.stack[-1].item, self.stack[-1].running

    def finished(self):
        '''Has test execution finished according to the plan?'''
        return self.peek_event() is None

    def skip_until(self, target_id, until_start, on_enter=None, on_exit=None):
        '''
        Advance through the plan until
            1. (if until_start is True) we are about to enter the target item;
            2. (if until_start is False) we have just exited from the target item.
        '''

        def reached_goal():
            item, enter = event
            return item.id == target_id and enter == until_start

        event = self.peek_event()
        while event is not None and not reached_goal():
            item, enter = event
            if enter:
                on_enter(item) if on_enter else self.do_nothing(item)
            else:
                on_exit(item) if on_exit else self.do_nothing(item)
            self.next_event()
            event = self.peek_event()

    def skip_all(self, on_enter=None, on_exit=None):
        '''Advance through the plan until the end.'''
        event = self.peek_event()
        while event is not None:
            item, enter = event
            if enter:
                on_enter(item) if on_enter else self.do_nothing(item)
            else:
                on_exit(item) if on_exit else self.do_nothing(item)
            self.next_event()
            event = self.peek_event()
