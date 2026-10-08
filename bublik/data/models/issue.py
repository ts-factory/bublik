# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import CheckConstraint, Q

from bublik.core.references import REF_CORE, resolve_ref
from bublik.data.models.project import Project
from bublik.data.models.result import Test, TestIterationResult


__all__ = [
    'Issue',
    'IssueCategory',
    'IssueRule',
    'IssueState',
    'RuleResult',
    'RuleResultOrigin',
    'default_expected_for',
]


BUG_KEY_VALIDATOR = RegexValidator(
    regex=f'^{REF_CORE}$',
    message='Bug key must be in ref://TRACKER/KEY form.',
)


class IssueState(models.TextChoices):
    OPEN = 'open'
    CLOSED = 'closed'


class IssueCategory(models.TextChoices):
    PRODUCT_DEFECT = 'product-defect'
    TEST_BUG = 'test-bug'
    ENV = 'env'
    KNOWN_ISSUE = 'known-issue'
    FLAKY = 'flaky'
    TO_INVESTIGATE = 'to-investigate'


class RuleResultOrigin(models.TextChoices):
    IMPORT = 'import'
    MANUAL_PERSISTENT = 'manual_persistent'
    MANUAL_ONEOFF = 'manual_oneoff'


_EXPECTED_BY_CATEGORY = {
    IssueCategory.KNOWN_ISSUE: True,
    IssueCategory.ENV: True,
    IssueCategory.TEST_BUG: True,
    IssueCategory.FLAKY: True,
    IssueCategory.PRODUCT_DEFECT: False,
    IssueCategory.TO_INVESTIGATE: False,
}


def default_expected_for(category):
    """Suggested `expected` flag for a category."""
    return _EXPECTED_BY_CATEGORY.get(category, False)


class Issue(models.Model):
    """
    A project-specific bug/cause record. Each row belongs to exactly one
    project - the same real-world bug filed against several projects is
    several separate Issue rows, each with its own bug_key/title/state.
    IssueRule attaches directly to an Issue - there's no intermediate
    per-project link table anymore.
    """

    title = models.CharField(max_length=512, help_text='Internal label.')
    description = models.TextField(null=True, blank=True, help_text='Internal notes.')
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='issues')
    state = models.CharField(
        max_length=16,
        choices=IssueState.choices,
        default=IssueState.OPEN,
        help_text='Bublik view of whether the bug is still active.',
    )
    bug_key = models.CharField(
        max_length=256,
        null=True,
        blank=True,
        validators=[BUG_KEY_VALIDATOR],
        help_text='External bug reference in ref://TRACKER/KEY form, or null if unlinked.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'bublik_issue'
        constraints = [
            models.UniqueConstraint(
                fields=['project', 'title'],
                name='issue_unique_project_title',
            ),
            models.UniqueConstraint(
                fields=['project', 'bug_key'],
                name='issue_unique_project_bug_key',
            ),
            CheckConstraint(
                condition=Q(bug_key__isnull=True) | Q(bug_key__regex=f'^{REF_CORE}$'),
                name='issue_bug_key_format',
            ),
        ]

    def __repr__(self):
        return (
            f'Issue(project_id={self.project_id!r}, title={self.title!r}, state={self.state!r})'
        )

    @property
    def bug_url(self):
        """Resolved tracker URL for `bug_key`, or None if unlinked/unconfigured."""
        return resolve_ref(self.bug_key, self.project_id)[2] if self.bug_key else None


class IssueRule(models.Model):
    """
    The triage decision: classification (category + expected) plus the
    matcher and lifecycle flag. Attaches directly to an Issue.
    """

    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name='rules')
    category = models.CharField(max_length=32, choices=IssueCategory.choices)
    expected = models.BooleanField(
        null=True,
        blank=True,
        help_text='Disposition: True=expected (suppresses), False=unexpected, '
        'None=none (marker only).',
    )
    active = models.BooleanField(
        default=True,
        help_text='Whether it applies to future imports.',
    )

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='issue_rules')
    parameters = models.JSONField(
        default=dict,
        blank=True,
        help_text='Captured {name: value} subset, matched against the result. '
        'Empty = parameters ignored.',
    )
    verdicts = models.JSONField(
        default=list,
        blank=True,
        help_text='Captured verdict strings, matched as a subset. Empty = ignored.',
    )
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text='Captured tag strings (important+relevant), matched as a subset. '
        'Empty = no tag gate.',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'bublik_issuerule'

    def __repr__(self):
        return (
            f'IssueRule(issue_id={self.issue_id!r}, category={self.category!r}, '
            f'expected={self.expected!r}, active={self.active!r})'
        )


class RuleResult(models.Model):
    """
    The table connects a test iteration result with an issue rule. Issue,
    category and expected are read through `issue_rule`.
    """

    result = models.ForeignKey(
        TestIterationResult,
        on_delete=models.CASCADE,
        related_name='rule_results',
        help_text='The test iteration result identifier.',
    )
    issue_rule = models.ForeignKey(
        IssueRule,
        on_delete=models.CASCADE,
        related_name='rule_results',
        help_text='The issue rule identifier.',
    )
    origin = models.CharField(
        max_length=32,
        choices=RuleResultOrigin.choices,
        help_text='How this result was classified.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    class Meta:
        db_table = 'bublik_ruleresult'
        constraints = [
            models.UniqueConstraint(
                fields=['result', 'issue_rule'],
                name='ruleresult_unique_result_rule',
            ),
        ]

    def __repr__(self):
        return (
            f'RuleResult(result_id={self.result_id!r}, rule_id={self.issue_rule_id!r}, '
            f'origin={self.origin!r})'
        )

    @property
    def is_suppressing(self) -> bool:
        """
        Whether this stamp makes its result's error not count as unexpected:
        true when its rule is expected and the rule's issue is still open.
        """
        return bool(self.issue_rule.expected) and self.issue_rule.issue.state == IssueState.OPEN
