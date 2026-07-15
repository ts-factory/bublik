# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers


class ActionResultSerializer(serializers.Serializer):
    requested = serializers.IntegerField(help_text='The number of distinct IDs requested.')
    updated = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='The IDs the action changed.',
    )
    unchanged = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='The IDs that were already in the requested state.',
    )
    not_found = serializers.ListField(
        child=serializers.IntegerField(),
        help_text='The IDs that do not exist.',
    )


class BulkIdsRequestSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)


def _counts_field(help_text):
    return serializers.DictField(child=serializers.IntegerField(), help_text=help_text)


class IssueFacetsSerializer(serializers.Serializer):
    """
    How many issues have each value of a filter of the issue list. Each
    filter is counted with itself left out and every other filter applied.
    """

    state = _counts_field('Issues per state: open, closed.')
    categories = _counts_field('Issues having an active rule of each category.')
    rules = _counts_field('Issues per rules state: enforced, dormant, deactivated, unruled.')
    project = _counts_field('Issues per project ID.')


class IssueRuleFacetsSerializer(serializers.Serializer):
    """
    How many rules have each value of a filter of the rule list. Each filter
    is counted with itself left out and every other filter applied.
    """

    active = _counts_field('Rules per active state: true, false.')
    category = _counts_field('Rules per category.')
    expected = _counts_field('Rules per disposition: expected, unexpected, none.')
    project = _counts_field('Rules per project ID.')
    issue_state = _counts_field("Rules per their issue's state: open, closed.")
    tag = _counts_field('Rules per captured tag, most common first.')
    verdict = _counts_field('Rules per captured verdict, most common first.')
    parameter = _counts_field(
        "Rules per captured parameter, as 'name=value', most common first.",
    )


class IssueListQuerySerializer(serializers.Serializer):
    state = serializers.CharField(required=False)
    project = serializers.IntegerField(required=False)
    category = serializers.CharField(required=False)
    rules = serializers.CharField(
        required=False,
        help_text="';'-separated rules states: enforced, dormant, deactivated, unruled.",
    )
    search = serializers.CharField(required=False)
    created_after = serializers.DateField(required=False)
    created_before = serializers.DateField(required=False)
    updated_after = serializers.DateField(required=False)
    updated_before = serializers.DateField(required=False)


class IssueRuleListQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False)
    issue = serializers.IntegerField(required=False)
    search = serializers.CharField(required=False)
    category = serializers.CharField(required=False)
    active = serializers.CharField(required=False)
    expected = serializers.CharField(
        required=False,
        help_text="';'-separated dispositions: expected, unexpected, none.",
    )
    issue_state = serializers.CharField(
        required=False,
        help_text="';'-separated issue states: open, closed.",
    )
    tag = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text='Repeatable. Rules that captured any of the given tags.',
    )
    verdict = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text='Repeatable. Rules that captured any of the given verdicts.',
    )
    parameter = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text="Repeatable 'name=value'. Rules that captured any of them.",
    )
    created_after = serializers.DateField(required=False)
    created_before = serializers.DateField(required=False)
