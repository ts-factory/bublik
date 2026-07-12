# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase

from bublik.core.run.classification import ClassificationService
from bublik.data import models
from bublik.tests import classification_data as data


IMPORT = models.RuleResultOrigin.IMPORT
MANUAL = models.RuleResultOrigin.MANUAL_PERSISTENT


class ClassificationDataMixin:
    """
    A run of `ping` (VM and HW iterations) and `reset`, tagged kernel=6.1 and
    lab=eu, where the VM ping failed with 'Packet lost', the HW ping failed
    with 'Packet lost' and 'Timeout', a second VM ping passed and `reset`
    failed.
    """

    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project, tags=[('kernel', '6.1'), ('lab', 'eu')])
        vm = data.iteration(cls.tree.ping, env='VM', mode='')
        hw = data.iteration(cls.tree.ping, env='HW', mode='')
        cls.vm_failed = data.result(cls.run_row, vm, verdicts=['Packet lost'])
        cls.hw_failed = data.result(cls.run_row, hw, verdicts=['Packet lost', 'Timeout'])
        cls.vm_passed = data.result(cls.run_row, vm, status='PASSED', unexpected=False)
        cls.reset_failed = data.result(cls.run_row, data.iteration(cls.tree.reset, env='HW'))
        cls.issue = data.issue(cls.project)

    def _stamps(self):
        return {
            (stamp.result_id, stamp.issue_rule_id, stamp.origin)
            for stamp in models.RuleResult.objects.filter(result__test_run=self.run_row)
        }


@data.isolated_caches
class MatchRulesTest(ClassificationDataMixin, TestCase):
    def _match(self, *rules):
        return ClassificationService.match_rules(list(rules), self.run_row)

    def test_only_unexpected_results_of_the_test_match(self):
        rule = data.rule(self.issue, self.tree.ping)
        assert sorted(self._match(rule)[rule.id]) == sorted(
            [self.vm_failed.id, self.hw_failed.id],
        )

    def test_parameters_are_a_subset_of_the_result_ones(self):
        by_parameter = data.rule(self.issue, self.tree.ping, parameters={'env': 'HW'})
        by_empty_value = data.rule(self.issue, self.tree.ping, parameters={'mode': ''})
        by_both = data.rule(
            self.issue,
            self.tree.ping,
            parameters={'env': 'VM'},
            verdicts=['Timeout'],
        )
        matches = self._match(by_parameter, by_empty_value, by_both)
        assert matches[by_parameter.id] == [self.hw_failed.id]
        assert sorted(matches[by_empty_value.id]) == sorted(
            [self.vm_failed.id, self.hw_failed.id],
        )
        assert matches[by_both.id] == []

    def test_verdicts_match_exactly_in_order(self):
        packet_lost = data.rule(self.issue, self.tree.ping, verdicts=['Packet lost'])
        both = data.rule(self.issue, self.tree.ping, verdicts=['Packet lost', 'Timeout'])
        reordered = data.rule(self.issue, self.tree.ping, verdicts=['Timeout', 'Packet lost'])
        matches = self._match(packet_lost, both, reordered)
        assert matches[packet_lost.id] == [self.vm_failed.id]
        assert matches[both.id] == [self.hw_failed.id]
        assert matches[reordered.id] == []

    def test_ignored_verdicts_and_no_verdicts_are_different(self):
        ignored = data.rule(self.issue, self.tree.reset)
        none = data.rule(self.issue, self.tree.reset, verdicts=[])
        none_on_ping = data.rule(self.issue, self.tree.ping, verdicts=[])
        any_on_ping = data.rule(self.issue, self.tree.ping)
        matches = self._match(ignored, none, none_on_ping, any_on_ping)
        assert matches[ignored.id] == [self.reset_failed.id]
        assert matches[none.id] == [self.reset_failed.id]
        assert matches[none_on_ping.id] == []
        assert sorted(matches[any_on_ping.id]) == sorted(
            [self.vm_failed.id, self.hw_failed.id],
        )

    def test_tags_are_a_gate_on_all_the_tags_of_the_run(self):
        # None of the run's tags is categorized: all of them count.
        tagged = data.rule(self.issue, self.tree.reset, tags=['kernel=6.1', 'lab=eu'])
        other = data.rule(self.issue, self.tree.reset, tags=['kernel=6.1', 'lab=us'])
        matches = self._match(tagged, other)
        assert matches[tagged.id] == [self.reset_failed.id]
        assert matches[other.id] == []

    def test_a_failing_rule_is_left_out_and_the_others_still_match(self):
        broken = data.rule(self.issue, self.tree.ping)
        models.IssueRule.objects.filter(id=broken.id).update(parameters='x')
        broken.refresh_from_db()
        good = data.rule(self.issue, self.tree.reset)
        with self.assertLogs('bublik.server', level='ERROR'):
            matches = self._match(broken, good)
        assert broken.id not in matches
        assert matches[good.id] == [self.reset_failed.id]

    def test_queries_do_not_grow_with_rules(self):
        rules = [
            data.rule(
                self.issue,
                test,
                parameters={'env': 'HW'},
                verdicts=['Packet lost', 'Timeout'],
                tags=['lab=eu'],
            )
            for test in [self.tree.ping, self.tree.reset] * 10
        ]
        # The candidates, their parameters, their verdicts and the run's tags.
        with self.assertNumQueries(4):
            matches = self._match(*rules)
        assert matches[rules[0].id] == [self.hw_failed.id]


@data.isolated_caches
class ApplyRulesOnDemandTest(ClassificationDataMixin, TestCase):
    def _apply(self, actor=None):
        return ClassificationService.apply_active_rules_manual(self.run_row, actor=actor)

    def test_adds_the_missing_stamps_only(self):
        rule = data.rule(self.issue, self.tree.ping)
        data.stamp(self.vm_failed, rule, IMPORT)
        actor = data.admin()

        assert self._apply(actor) == 1
        assert self._stamps() == {
            (self.vm_failed.id, rule.id, IMPORT),
            (self.hw_failed.id, rule.id, MANUAL),
        }
        assert models.RuleResult.objects.get(result=self.hw_failed).created_by == actor
        assert self._apply() == 0

    def test_inactive_rules_and_rules_of_closed_issues_do_not_apply(self):
        data.rule(self.issue, self.tree.ping, active=False)
        closed = data.issue(self.project, title='closed', state=models.IssueState.CLOSED)
        data.rule(closed, self.tree.ping)
        assert self._apply() == 0
        assert self._stamps() == set()
