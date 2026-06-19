# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from datetime import datetime, timezone

from django.test import TestCase

from bublik.core.run.classification import apply_active_rules
from bublik.data.models import (
    Issue,
    IssueCategory,
    IssueRule,
    Meta,
    MetaResult,
    Project,
    ResultClassification,
    StampOrigin,
    Test,
    TestIteration,
    TestIterationResult,
)


class EngineFixtureMixin:
    _seq = 0

    @classmethod
    def _h(cls, p):
        cls._seq += 1
        return f'{p}-{cls._seq}'

    def make_run(self, project):
        return TestIterationResult.objects.create(
            iteration=None, test_run=None,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc), project=project,
        )

    def add_result(self, run, project, test, *, params=None, verdicts=(), err=False):
        iteration = TestIteration.objects.create(test=test, hash=self._h('iter'))
        for name, value in (params or {}).items():
            from bublik.data.models import TestArgument
            arg, _ = TestArgument.objects.get_or_create(
                name=name, value=value, defaults={'hash': self._h('arg')},
            )
            iteration.test_arguments.add(arg)
        result = TestIterationResult.objects.create(
            iteration=iteration, test_run=run,
            start=datetime(2026, 1, 1, tzinfo=timezone.utc), project=project,
        )
        if err:
            m = Meta.objects.create(type='err', value='FAILED', hash=self._h('err'))
            MetaResult.objects.create(result=result, meta=m, serial=0)
        for i, v in enumerate(verdicts):
            m = Meta.objects.create(type='verdict', value=v, hash=self._h('vd'))
            MetaResult.objects.create(result=result, meta=m, serial=i)
        return result

    def make_rule(self, project, test, *, params=None, verdicts=None, active=True,
                  match_parameters=True, match_verdicts=True,
                  match_important_tags=False, match_all_tags=False, tags=None):
        issue = Issue.objects.create(title='cause')
        return IssueRule.objects.create(
            project=project, issue=issue, test=test,
            category=IssueCategory.KNOWN_ISSUE, expected=True, active=active,
            match_parameters=match_parameters, match_verdicts=match_verdicts,
            match_important_tags=match_important_tags, match_all_tags=match_all_tags,
            parameters=params or {}, verdicts=verdicts or [], tags=tags or [],
        )


class ApplyActiveRulesTest(EngineFixtureMixin, TestCase):
    def test_stamps_matching_result(self):
        project = Project.objects.create(name=self._h('p'))
        run = self.make_run(project)
        test = Test.objects.create(name=self._h('t'), result_type='T')
        result = self.add_result(run, project, test, params={'a': '1'}, verdicts=['boom'], err=True)
        self.make_rule(project, test, params={'a': '1'}, verdicts=['boom'])

        apply_active_rules(run)

        stamp = ResultClassification.objects.get(result=result)
        self.assertEqual(stamp.origin, StampOrigin.IMPORT)

    def test_param_mismatch_not_stamped(self):
        project = Project.objects.create(name=self._h('p'))
        run = self.make_run(project)
        test = Test.objects.create(name=self._h('t'), result_type='T')
        result = self.add_result(run, project, test, params={'a': '2'}, verdicts=['boom'], err=True)
        self.make_rule(project, test, params={'a': '1'}, verdicts=['boom'])

        apply_active_rules(run)

        self.assertFalse(ResultClassification.objects.filter(result=result).exists())

    def test_verdict_mismatch_not_stamped(self):
        project = Project.objects.create(name=self._h('p'))
        run = self.make_run(project)
        test = Test.objects.create(name=self._h('t'), result_type='T')
        result = self.add_result(run, project, test, params={'a': '1'}, verdicts=['other'], err=True)
        self.make_rule(project, test, params={'a': '1'}, verdicts=['boom'])

        apply_active_rules(run)

        self.assertFalse(ResultClassification.objects.filter(result=result).exists())

    def test_inactive_rule_not_applied(self):
        project = Project.objects.create(name=self._h('p'))
        run = self.make_run(project)
        test = Test.objects.create(name=self._h('t'), result_type='T')
        result = self.add_result(run, project, test, params={'a': '1'}, verdicts=['boom'], err=True)
        self.make_rule(project, test, params={'a': '1'}, verdicts=['boom'], active=False)

        apply_active_rules(run)

        self.assertFalse(ResultClassification.objects.filter(result=result).exists())

    def test_reimport_recompute_drops_stopped_rule(self):
        project = Project.objects.create(name=self._h('p'))
        run = self.make_run(project)
        test = Test.objects.create(name=self._h('t'), result_type='T')
        result = self.add_result(run, project, test, params={'a': '1'}, verdicts=['boom'], err=True)
        rule = self.make_rule(project, test, params={'a': '1'}, verdicts=['boom'])

        apply_active_rules(run)
        self.assertTrue(ResultClassification.objects.filter(result=result).exists())

        rule.active = False
        rule.save()
        apply_active_rules(run)  # re-import
        self.assertFalse(ResultClassification.objects.filter(result=result).exists())

    def test_manual_stamp_preserved_on_recompute(self):
        project = Project.objects.create(name=self._h('p'))
        run = self.make_run(project)
        test = Test.objects.create(name=self._h('t'), result_type='T')
        result = self.add_result(run, project, test, params={'a': '1'}, verdicts=['boom'], err=True)
        rule = self.make_rule(project, test, params={'a': '1'}, verdicts=['boom'], active=False)
        ResultClassification.objects.create(
            result=result, rule=rule, origin=StampOrigin.MANUAL_ONEOFF,
        )

        apply_active_rules(run)  # rule inactive; manual stamp must survive

        stamp = ResultClassification.objects.get(result=result)
        self.assertEqual(stamp.origin, StampOrigin.MANUAL_ONEOFF)
