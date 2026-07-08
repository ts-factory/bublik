# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase, TestCase

from bublik.core.classification import Effect, RulesState, effect_for, rules_state_for
from bublik.data import models
from bublik.data.serializers import IssueRuleSerializer, IssueSerializer
from bublik.tests import classification_data as data


class EffectAndRulesStateTest(SimpleTestCase):
    def test_effect(self):
        assert effect_for(True, True, models.IssueState.OPEN) == Effect.SUPPRESSED
        assert effect_for(False, True, models.IssueState.CLOSED) == Effect.STALE
        assert effect_for(False, False, models.IssueState.CLOSED) == Effect.STALE
        assert effect_for(False, False, models.IssueState.OPEN) == Effect.UNEXPECTED
        assert effect_for(False, None, models.IssueState.OPEN) == Effect.MARKED

    def test_rules_state(self):
        assert rules_state_for(False, False, models.IssueState.OPEN) == RulesState.UNRULED
        assert rules_state_for(True, True, models.IssueState.CLOSED) == RulesState.ENFORCED
        assert rules_state_for(True, False, models.IssueState.CLOSED) == RulesState.DEACTIVATED
        assert rules_state_for(True, False, models.IssueState.OPEN) == RulesState.DORMANT


@data.isolated_caches
class IssueRuleSerializerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.other_project = models.Project.objects.create(name='other')
        cls.tree = data.TestTree()
        cls.run_row = data.run(cls.project)
        cls.result = data.result(cls.run_row, data.iteration(cls.tree.ping, env='VM'))
        cls.issue = data.issue(cls.project)

    def _validate(self, payload, instance=None):
        serializer = IssueRuleSerializer(instance, data=payload, partial=instance is not None)
        serializer.is_valid()
        return serializer

    def _payload(self, **fields):
        return {
            'issue': self.issue.id,
            'test': self.tree.ping.id,
            'category': models.IssueCategory.FLAKY,
            'parameters': {},
            'verdicts': None,
            'tags': [],
            **fields,
        }

    def test_expected_defaults_from_category(self):
        serializer = self._validate(self._payload(category=models.IssueCategory.PRODUCT_DEFECT))
        assert serializer.validated_data['expected'] is False
        serializer = self._validate(self._payload(expected=None))
        assert serializer.validated_data['expected'] is None

    def test_verdicts_ignored_and_none_are_different(self):
        serializer = self._validate(self._payload())
        assert serializer.validated_data['verdicts'] is None
        serializer = self._validate(self._payload(verdicts=[]))
        assert serializer.validated_data['verdicts'] == []

    def test_matcher_fields_must_be_given_on_create(self):
        for field in ('parameters', 'verdicts', 'tags'):
            payload = self._payload()
            del payload[field]
            serializer = self._validate(payload)
            assert set(serializer.errors) == {field}, field

    def test_matcher_types_are_checked(self):
        for field, value in (
            ('parameters', 'x'),
            ('parameters', {'env': ['VM']}),
            ('verdicts', 'Timeout'),
            ('verdicts', [['Timeout']]),
            ('tags', {'a': 'b'}),
        ):
            serializer = self._validate(self._payload(**{field: value}))
            assert field in serializer.errors, (field, value)

    def test_repeated_tags_are_rejected(self):
        serializer = self._validate(self._payload(tags=['a=1', 'b=2', 'a=1']))
        assert serializer.errors == {'tags': ['Repeated values: a=1.']}

    def test_tags_are_kept_sorted(self):
        run_row = data.run(self.project, tags=[('nightly', None), ('kernel', '6.1')])
        data.result(run_row, data.iteration(self.tree.ping))
        serializer = self._validate(self._payload(tags=['nightly', 'kernel=6.1']))
        assert serializer.validated_data['tags'] == ['kernel=6.1', 'nightly']

    def test_matcher_values_are_not_trimmed(self):
        data.result(
            self.run_row,
            data.iteration(self.tree.ping, env=' VM '),
            verdicts=[' Timeout'],
        )
        serializer = self._validate(
            self._payload(parameters={'env': ' VM '}, verdicts=[' Timeout']),
        )
        assert serializer.validated_data['parameters'] == {'env': ' VM '}
        assert serializer.validated_data['verdicts'] == [' Timeout']

    def test_matcher_values_must_be_in_the_database(self):
        other_run = data.run(self.other_project, tags=[('lab', 'eu')])
        data.result(other_run, data.iteration(self.tree.ping), verdicts=['Reset'])
        data.result(self.run_row, data.iteration(self.tree.reset, mode='fast'))
        # Tags of another project's runs, parameters of another test, and
        # verdicts of results in another project are not this rule's to use.
        serializer = self._validate(
            self._payload(
                parameters={'env': 'VM', 'mode': 'fast'},
                verdicts=['Timeout', 'Reset'],
                tags=['lab=eu'],
            ),
        )
        assert serializer.errors == {
            'tags': ["Not among the tags of the runs of project 'proj': lab=eu."],
            'parameters': ["Not among the parameters of test 'ping': mode=fast."],
            'verdicts': [
                (
                    "Not among the verdicts of the results of test 'ping' in project 'proj': "
                    'Timeout, Reset.'
                ),
            ],
        }

    def test_matcher_values_found_in_the_database_are_accepted(self):
        run_row = data.run(self.project, tags=[('nightly', ''), ('kernel', '6.1')])
        data.result(run_row, data.iteration(self.tree.ping, env='VM'), verdicts=['Timeout'])
        serializer = self._validate(
            self._payload(
                parameters={'env': 'VM'},
                verdicts=['Timeout', 'Timeout'],
                tags=['nightly', 'kernel=6.1'],
            ),
        )
        assert serializer.errors == {}

    def test_only_a_test_can_be_ruled(self):
        # With results of their own, so that it is not the check for results
        # in the project that rejects them.
        data.result(self.run_row, self.tree.net_iteration)
        data.result(self.run_row, self.tree.suite_iteration)
        for test in (self.tree.net, self.tree.suite):
            serializer = self._validate(self._payload(test=test.id))
            assert 'not a test' in str(serializer.errors.get('test')), test.name

    def test_test_must_have_results_in_the_project(self):
        other_issue = data.issue(self.other_project)
        serializer = self._validate(self._payload(issue=other_issue.id))
        assert 'test' in serializer.errors

    def test_closed_issue_takes_no_rules(self):
        closed = data.issue(self.project, title='closed', state=models.IssueState.CLOSED)
        serializer = self._validate(self._payload(issue=closed.id))
        assert 'issue' in serializer.errors

    def test_matcher_is_locked_once_stamped(self):
        rule = data.rule(self.issue, self.tree.ping, parameters={'env': 'VM'})
        data.stamp(self.result, rule)

        # The current values are not a change, and the disposition may change.
        serializer = self._validate(
            {
                'parameters': {'env': 'VM'},
                'verdicts': None,
                'category': models.IssueCategory.ENV,
            },
            instance=rule,
        )
        assert serializer.errors == {}

        serializer = self._validate({'parameters': {'env': 'HW'}, 'tags': ['x']}, instance=rule)
        assert set(serializer.errors) == {'parameters', 'tags'}

        # Ignored verdicts and no verdicts are different matchers.
        serializer = self._validate({'verdicts': []}, instance=rule)
        assert set(serializer.errors) == {'verdicts'}

    def test_tags_in_another_order_are_not_a_change(self):
        rule = data.rule(self.issue, self.tree.ping, tags=['a=1', 'b=2'])
        data.stamp(self.result, rule)
        serializer = self._validate({'tags': ['b=2', 'a=1']}, instance=rule)
        assert serializer.errors == {}

    def test_matcher_may_change_before_stamped(self):
        data.result(self.run_row, data.iteration(self.tree.ping, env='HW'))
        rule = data.rule(self.issue, self.tree.ping)
        serializer = self._validate({'parameters': {'env': 'HW'}}, instance=rule)
        assert serializer.errors == {}

    def test_changed_matcher_is_checked_against_the_database(self):
        rule = data.rule(self.issue, self.tree.ping, parameters={'env': 'gone'})
        # Unchanged values are not checked again.
        serializer = self._validate({'category': models.IssueCategory.ENV}, instance=rule)
        assert serializer.errors == {}
        # A change checks the whole resulting matcher.
        serializer = self._validate({'verdicts': []}, instance=rule)
        assert set(serializer.errors) == {'parameters'}


@data.isolated_caches
class IssueSerializerTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = models.Project.objects.create(name='proj')
        cls.tree = data.TestTree()
        cls.result = data.result(data.run(cls.project), data.iteration(cls.tree.ping))
        cls.issue = data.issue(cls.project, bug_key='ref://JIRA/FOO-1')

    def _validate(self, payload, instance=None):
        serializer = IssueSerializer(instance, data=payload, partial=instance is not None)
        serializer.is_valid()
        return serializer

    def test_empty_bug_key_is_none(self):
        serializer = self._validate({'project': self.project.id, 'title': 'new', 'bug_key': ''})
        assert serializer.errors == {}
        assert serializer.validated_data['bug_key'] is None

    def test_bug_key_format(self):
        serializer = self._validate(
            {'project': self.project.id, 'title': 'new', 'bug_key': 'JIRA-1'},
        )
        assert 'bug_key' in serializer.errors
        serializer = self._validate(
            {'project': self.project.id, 'title': 'new', 'bug_key': 'ref://GH/org/repo#12'},
        )
        assert serializer.errors == {}

    def test_uniqueness_is_reported_under_the_field(self):
        serializer = self._validate({'project': self.project.id, 'title': 'issue'})
        assert set(serializer.errors) == {'title'}
        serializer = self._validate(
            {'project': self.project.id, 'title': 'new', 'bug_key': 'ref://JIRA/FOO-1'},
        )
        assert set(serializer.errors) == {'bug_key'}

    def test_issues_without_bug_key_do_not_clash(self):
        data.issue(self.project, title='no key 1')
        serializer = self._validate({'project': self.project.id, 'title': 'no key 2'})
        assert serializer.errors == {}

    def test_bug_key_is_locked_once_classified(self):
        rule = data.rule(self.issue, self.tree.ping)
        data.stamp(self.result, rule)
        serializer = self._validate({'bug_key': 'ref://JIRA/FOO-1'}, instance=self.issue)
        assert serializer.errors == {}
        serializer = self._validate({'bug_key': 'ref://JIRA/FOO-2'}, instance=self.issue)
        assert set(serializer.errors) == {'bug_key'}

    def test_rules_must_fit_a_new_project(self):
        run_row = data.run(self.project, tags=[('lab', 'eu')])
        data.result(run_row, data.iteration(self.tree.ping), verdicts=['Timeout'])
        issue = data.issue(self.project, title='to move')
        data.rule(issue, self.tree.ping, tags=['lab=eu'], verdicts=['Timeout'])
        other_project = models.Project.objects.create(name='other')
        data.result(data.run(other_project), data.iteration(self.tree.ping))
        serializer = self._validate({'project': other_project.id}, instance=issue)
        assert serializer.errors == {
            'project': [
                (
                    'Tags of the issue rules are not among the tags of the runs of project '
                    "'other': lab=eu."
                ),
                (
                    'Verdicts of the issue rules are not among the verdicts of the results of '
                    "their tests in project 'other': Timeout."
                ),
            ],
        }
        other_run = data.run(other_project, tags=[('lab', 'eu')])
        data.result(other_run, data.iteration(self.tree.ping), verdicts=['Timeout'])
        serializer = self._validate({'project': other_project.id}, instance=issue)
        assert serializer.errors == {}

    def test_rules_state(self):
        assert IssueSerializer(self.issue).data['rules_state'] == RulesState.UNRULED
        rule = data.rule(self.issue, self.tree.ping)
        assert IssueSerializer(self.issue).data['rules_state'] == RulesState.ENFORCED
        rule.active = False
        rule.save()
        assert IssueSerializer(self.issue).data['rules_state'] == RulesState.DORMANT
