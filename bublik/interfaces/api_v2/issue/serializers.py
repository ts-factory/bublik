# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from rest_framework import serializers
from rest_framework.fields import empty

from bublik.core.issue_results import (
    IssueResultsService,
    ResultClassification,
    ResultProperty,
)
from bublik.data.models import IssueCategory, ResultStatus
from bublik.interfaces.api_v2.result.serializers import ResultDetailsSerializer
from bublik.interfaces.api_v2.run.serializers import RunIssueRuleSerializer


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


class DelimitedChoicesField(serializers.ListField):
    """
    A `;`-separated list of choices in one query parameter, the form the
    other listings take their enum-valued filters in.
    """

    def __init__(self, choices, **kwargs):
        super().__init__(child=serializers.ChoiceField(choices=choices), **kwargs)

    def get_value(self, dictionary):
        return dictionary.get(self.field_name, empty)

    def to_internal_value(self, data):
        if isinstance(data, str):
            data = data.split(settings.QUERY_DELIMITER) if data else []
        return super().to_internal_value(data)


class IssueResultsQuerySerializer(serializers.Serializer):
    """
    The result table's filter bar, as query parameters. Repeatable
    parameters are repeated, one value each; the others take a
    `;`-separated list, as the plain result listing does.
    """

    page = serializers.IntegerField(required=False, min_value=1)
    page_size = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=IssueResultsService.MAX_PAGE_SIZE,
    )
    results = serializers.CharField(
        required=False,
        help_text=f"';'-separated obtained results: {', '.join(ResultStatus.all_statuses())}.",
    )
    result_properties = DelimitedChoicesField(
        ResultProperty.choices,
        required=False,
        help_text=f"';'-separated: {', '.join(ResultProperty.values)}.",
    )
    requirements = serializers.CharField(
        required=False,
        help_text="';'-separated. Results carrying every given requirement.",
    )
    category = DelimitedChoicesField(
        IssueCategory.choices,
        required=False,
        help_text=f"';'-separated stamp categories: {', '.join(IssueCategory.values)}.",
    )
    classification = DelimitedChoicesField(
        ResultClassification.choices,
        required=False,
        help_text=f"';'-separated: {', '.join(ResultClassification.values)}.",
    )
    verdict = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text='Repeatable. Results carrying every given verdict.',
    )
    artifact = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text='Repeatable. Results carrying every given artifact.',
    )
    parameter = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        help_text=(
            "Repeatable 'name=value', or a bare name for an empty value. "
            'Results whose iteration carries every one.'
        ),
    )


class IssueResultsFacetsSerializer(serializers.Serializer):
    """
    Every value each filter can take over the issue's results, unfiltered.
    Served on its own: it depends on the issue alone, not on the page.
    """

    results = serializers.ListField(child=serializers.CharField())
    result_properties = serializers.ListField(child=serializers.CharField())
    verdicts = serializers.ListField(child=serializers.CharField())
    categories = serializers.ListField(child=serializers.CharField())
    classifications = serializers.ListField(child=serializers.CharField())
    artifacts = serializers.ListField(child=serializers.CharField())
    parameters = serializers.ListField(child=serializers.CharField())
    requirements = serializers.ListField(child=serializers.CharField())


class IssueResultsPaginationSerializer(serializers.Serializer):
    page = serializers.IntegerField()
    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)


class RunTagsSerializer(serializers.Serializer):
    important_tags = serializers.ListField(child=serializers.CharField())
    relevant_tags = serializers.ListField(child=serializers.CharField())


class IssueResultDetailsSerializer(ResultDetailsSerializer):
    """A result row with its run's tags, since the rows span runs."""

    run_tags = RunTagsSerializer()


class IssueResultsResponseSerializer(serializers.Serializer):
    pagination = IssueResultsPaginationSerializer()
    results = IssueResultDetailsSerializer(many=True)


class IssuePickerQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False)
    search = serializers.CharField(required=False)
    state = serializers.CharField(
        required=False,
        help_text="';'-separated issue states: open, closed.",
    )
    test = serializers.CharField(
        required=False,
        help_text='Test ID or name. Keeps the issues having a rule for the test.',
    )


class IssuePickerOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    state = serializers.CharField()
    bug_key = serializers.CharField(allow_null=True)
    bug_url = serializers.CharField(allow_null=True)
    rules = RunIssueRuleSerializer(many=True)


class TestPickerQuerySerializer(serializers.Serializer):
    project = serializers.IntegerField(required=False)
    search = serializers.CharField(required=False)


class TestPickerOptionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    path = serializers.CharField()
