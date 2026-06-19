# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar

from django.conf import settings
from django.db import models

from bublik.data.models.project import Project
from bublik.data.models.result import Test, TestIterationResult


__all__ = [
    'Issue',
    'IssueCategory',
    'IssueExt',
    'IssueRule',
    'IssueState',
    'ResultClassification',
    'StampOrigin',
    'default_expected_for',
]


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


class StampOrigin(models.TextChoices):
    IMPORT = 'import'
    MANUAL_APPLY = 'manual_apply'
    MANUAL_ONEOFF = 'manual_oneoff'


# Default `expected` flag suggested by category. Editable per rule.
_EXPECTED_BY_CATEGORY: ClassVar[dict] = {
    IssueCategory.KNOWN_ISSUE: True,
    IssueCategory.ENV: True,
    IssueCategory.TEST_BUG: True,
    IssueCategory.FLAKY: True,
    IssueCategory.PRODUCT_DEFECT: False,
    IssueCategory.TO_INVESTIGATE: False,
}


def default_expected_for(category):
    '''Suggested `expected` flag for a category (see spec 3.3).'''
    return _EXPECTED_BY_CATEGORY.get(category, False)


class IssueExt(models.Model):
    '''
    Cache of an external tracker bug. Key is set at triage; status/title are
    populated by the later issue-tracker connector (1b), null until then.
    '''

    key = models.CharField(
        max_length=256,
        unique=True,
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

    def __repr__(self):
        return f'IssueExt(key={self.key!r}, status={self.status!r})'


class Issue(models.Model):
    '''
    The cause identity. Global (cross-project), dedup point. Carries no
    classification - category and expected live on the rule.
    '''

    issue_ext = models.OneToOneField(
        IssueExt,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='issue',
        help_text='1:1 link to the external cache, or null for internal-only.',
    )
    title = models.CharField(max_length=512, help_text='Internal label.')
    description = models.TextField(null=True, blank=True, help_text='Internal notes.')
    state = models.CharField(
        max_length=16,
        choices=IssueState.choices,
        default=IssueState.OPEN,
        help_text='Bublik view of whether the bug is still active.',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'bublik_issue'

    def __repr__(self):
        return f'Issue(title={self.title!r}, state={self.state!r})'


class IssueRule(models.Model):
    '''
    The triage decision: classification (category + expected) plus the matcher
    and lifecycle flag. Per-project, points at a global Issue.
    '''

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='issue_rules')
    issue = models.ForeignKey(Issue, on_delete=models.CASCADE, related_name='rules')
    category = models.CharField(max_length=32, choices=IssueCategory.choices)
    expected = models.BooleanField(help_text='Whether matches suppress the unexpected count.')
    active = models.BooleanField(default=True, help_text='Whether it applies to future imports.')

    test = models.ForeignKey(Test, on_delete=models.CASCADE, related_name='issue_rules')
    match_parameters = models.BooleanField(default=True)
    match_verdicts = models.BooleanField(default=True)
    match_important_tags = models.BooleanField(default=True)
    match_all_tags = models.BooleanField(default=False)
    parameters = models.JSONField(default=dict, blank=True, help_text='Captured {name: value}.')
    verdicts = models.JSONField(default=list, blank=True, help_text='Captured verdict strings.')
    tags = models.JSONField(default=list, blank=True, help_text='Captured tag strings.')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    updated_at = models.DateTimeField(auto_now=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'bublik_issuerule'

    def __repr__(self):
        return (
            f'IssueRule(issue_id={self.issue_id!r}, category={self.category!r}, '
            f'expected={self.expected!r}, active={self.active!r})'
        )


class ResultClassification(models.Model):
    '''
    Per-result stamp produced by applying a rule. Issue, category and expected
    are read through `rule`.
    '''

    result = models.ForeignKey(
        TestIterationResult,
        on_delete=models.CASCADE,
        related_name='classifications',
    )
    rule = models.ForeignKey(IssueRule, on_delete=models.CASCADE, related_name='stamps')
    origin = models.CharField(max_length=16, choices=StampOrigin.choices)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bublik_resultclassification'
        unique_together = (('result', 'rule'),)

    def __repr__(self):
        return (
            f'ResultClassification(result_id={self.result_id!r}, '
            f'rule_id={self.rule_id!r}, origin={self.origin!r})'
        )
