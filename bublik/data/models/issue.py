# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import CheckConstraint, Q

from bublik.core.references import REF_CORE
from bublik.data.models.project import Project
from bublik.data.models.result import Test, TestIterationResult


__all__ = [
    'Issue',
    'IssueCategory',
    'IssueExt',
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


# Default `expected` flag suggested by category. Editable per rule.
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


class IssueExt(models.Model):
    """
    Cache of an external tracker bug. Key is set at triage; status/title are
    populated by the later issue-tracker connector, null until then.
    """

    key = models.CharField(
        max_length=256,
        unique=True,
        validators=[BUG_KEY_VALIDATOR],
        help_text='External bug reference in ref://TRACKER/KEY form.',
    )
    status = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        help_text='Cached external status. Populated by the connector.',
    )
    title = models.CharField(
        max_length=512,
        null=True,
        blank=True,
        help_text='Cached external summary. Populated by the connector.',
    )
    synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last successful sync with the tracker.',
    )
    raw = models.JSONField(
        null=True,
        blank=True,
        help_text='Optional raw tracker payload cache.',
    )

    class Meta:
        db_table = 'bublik_issue_ext'
        constraints = [
            CheckConstraint(
                condition=Q(key__regex=f'^{REF_CORE}$'),
                name='issueext_key_format',
            ),
        ]

    def __repr__(self):
        return f'IssueExt(key={self.key!r}, status={self.status!r})'


class Issue(models.Model):
    """
    The cause identity. Global (cross-project), dedup point. Carries no
    classification - category and expected live on the rule.
    """

    title = models.CharField(max_length=512, help_text='Internal label.')
    description = models.TextField(null=True, blank=True, help_text='Internal notes.')
    state = models.CharField(
        max_length=16,
        choices=IssueState.choices,
        default=IssueState.OPEN,
        help_text='Bublik view of whether the bug is still active.',
    )
    issue_ext = models.OneToOneField(
        IssueExt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issue',
        help_text='1:1 link to the external cache, or null for internal-only.',
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

    def __repr__(self):
        return f'Issue(title={self.title!r}, state={self.state!r})'


class IssueRule(models.Model):
    """
    The triage decision: classification (category + expected) plus the matcher
    and lifecycle flag. Per-project, points at a global Issue.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='issue_rules')
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
    The table connects a test iteration result with an issue rule.
    Issue, category and expected are read through `issue_rule`.
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
                fields=['result', 'issue_rule'], name='ruleresult_unique_result_rule'
            ),
        ]

    def __repr__(self):
        return (
            f'RuleResult(result_id={self.result_id!r}, '
            f'rule_id={self.issue_rule_id!r}, origin={self.origin!r})'
        )
