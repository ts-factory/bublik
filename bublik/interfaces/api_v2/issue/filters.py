# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import typing

from django import forms
from django.conf import settings
from django.db.models import Q
import django_filters
from django_filters import rest_framework as filters
from django_filters.widgets import QueryArrayWidget
from rest_framework.exceptions import ValidationError

from bublik.core.classification import (
    DISPOSITION_TO_EXPECTED,
    RulesState,
    disposition_query,
    rules_state_query,
)
from bublik.core.datetime_formatting import parse_date_param
from bublik.data.models import Issue, IssueCategory, IssueRule, IssueState


class MultiValueField(forms.Field):
    widget = QueryArrayWidget


class MultiValueCharFilter(django_filters.Filter):
    """
    A repeatable string param (`?verdict=a&verdict=b`), handed to the filter
    method as a list. For free-text values - verdicts, tags, parameter
    values - which may contain the `;` that the enum params split on.
    """

    field_class = MultiValueField


def split_parameter(value):
    """'name=value' -> ('name', 'value'), split on the first KEY_VALUE_DELIMITER."""
    name, sep, param_value = value.partition(settings.KEY_VALUE_DELIMITER)
    if not sep or not name:
        raise ValidationError({'parameter': f"Expected 'name=value', got {value!r}."})
    return name, param_value


class IssueFilterSet(filters.FilterSet):
    project = django_filters.NumberFilter(field_name='project_id')
    state = django_filters.CharFilter(method='filter_state')
    category = django_filters.CharFilter(method='filter_category')
    rules = django_filters.CharFilter(method='filter_rules')
    search = django_filters.CharFilter(method='filter_search')
    created_after = django_filters.CharFilter(method='filter_created_after')
    created_before = django_filters.CharFilter(method='filter_created_before')
    updated_after = django_filters.CharFilter(method='filter_updated_after')
    updated_before = django_filters.CharFilter(method='filter_updated_before')

    class Meta:
        model = Issue
        fields: typing.ClassVar = []

    def filter_state(self, qs, name, value):
        states = value.split(settings.QUERY_DELIMITER)
        if any(state not in IssueState.values for state in states):
            msg = f'Expected any of: {", ".join(IssueState.values)}.'
            raise ValidationError({'state': msg})
        return qs.filter(state__in=states)

    def filter_category(self, qs, name, value):
        categories = value.split(settings.QUERY_DELIMITER)
        if any(category not in IssueCategory.values for category in categories):
            msg = f'Expected any of: {", ".join(IssueCategory.values)}.'
            raise ValidationError({'category': msg})
        return qs.filter(
            pk__in=Issue.objects.filter(
                rules__category__in=categories,
                rules__active=True,
            ).values('pk'),
        )

    def filter_rules(self, qs, name, value):
        rule_states = value.split(settings.QUERY_DELIMITER)
        if any(rule_state not in RulesState.values for rule_state in rule_states):
            msg = f'Expected any of: {", ".join(RulesState.values)}.'
            raise ValidationError({'rules': msg})
        return qs.filter(rules_state_query(rule_states))

    def filter_search(self, qs, name, value):
        query = (
            Q(title__icontains=value)
            | Q(description__icontains=value)
            | Q(bug_key__icontains=value)
        )
        if value.startswith('#') and value[1:].isdigit():
            query |= Q(id=int(value[1:]))
        return qs.filter(query)

    def filter_created_after(self, qs, name, value):
        date = parse_date_param(value, 'created_after')
        return qs.filter(created_at__date__gte=date)

    def filter_created_before(self, qs, name, value):
        date = parse_date_param(value, 'created_before')
        return qs.filter(created_at__date__lte=date)

    def filter_updated_after(self, qs, name, value):
        date = parse_date_param(value, 'updated_after')
        return qs.filter(updated_at__date__gte=date)

    def filter_updated_before(self, qs, name, value):
        date = parse_date_param(value, 'updated_before')
        return qs.filter(updated_at__date__lte=date)


class IssueRuleFilterSet(filters.FilterSet):
    project = django_filters.NumberFilter(field_name='issue__project_id')
    issue = django_filters.NumberFilter(field_name='issue_id')
    search = django_filters.CharFilter(method='filter_search')
    category = django_filters.CharFilter(method='filter_category')
    active = django_filters.CharFilter(method='filter_active')
    expected = django_filters.CharFilter(method='filter_expected')
    issue_state = django_filters.CharFilter(method='filter_issue_state')
    tag = MultiValueCharFilter(method='filter_tag')
    verdict = MultiValueCharFilter(method='filter_verdict')
    parameter = MultiValueCharFilter(method='filter_parameter')
    created_after = django_filters.CharFilter(method='filter_created_after')
    created_before = django_filters.CharFilter(method='filter_created_before')

    class Meta:
        model = IssueRule
        fields: typing.ClassVar = []

    def filter_search(self, qs, name, value):
        return qs.filter(
            Q(test__name__icontains=value)
            | Q(issue__title__icontains=value)
            | Q(issue__bug_key__icontains=value),
        )

    def filter_category(self, qs, name, value):
        categories = value.split(settings.QUERY_DELIMITER)
        if any(category not in IssueCategory.values for category in categories):
            msg = f'Expected any of: {", ".join(IssueCategory.values)}.'
            raise ValidationError({'category': msg})
        return qs.filter(category__in=categories)

    def filter_active(self, qs, name, value):
        bool_values = {'true': True, 'false': False}
        values = value.split(settings.QUERY_DELIMITER)
        if any(v.lower() not in bool_values for v in values):
            raise ValidationError({'active': "Expected 'true' and/or 'false'."})
        return qs.filter(active__in=[bool_values[v.lower()] for v in values])

    def filter_expected(self, qs, name, value):
        dispositions = value.split(settings.QUERY_DELIMITER)
        if any(d not in DISPOSITION_TO_EXPECTED for d in dispositions):
            msg = "Expected 'expected', 'unexpected', and/or 'none'."
            raise ValidationError({'expected': msg})
        return qs.filter(disposition_query(dispositions))

    def filter_issue_state(self, qs, name, value):
        states = value.split(settings.QUERY_DELIMITER)
        if any(state not in IssueState.values for state in states):
            msg = f'Expected any of: {", ".join(IssueState.values)}.'
            raise ValidationError({'issue_state': msg})
        return qs.filter(issue__state__in=states)

    # The matcher filters keep a rule that captured any of the given values,
    # as the facet options count them: one rule per value it carries.

    def filter_tag(self, qs, name, values):
        query = Q()
        for tag in values:
            query |= Q(tags__contains=[tag])
        return qs.filter(query)

    def filter_verdict(self, qs, name, values):
        query = Q()
        for verdict in values:
            query |= Q(verdicts__contains=[verdict])
        return qs.filter(query)

    def filter_parameter(self, qs, name, values):
        query = Q()
        for value in values:
            param_name, param_value = split_parameter(value)
            query |= Q(parameters__contains={param_name: param_value})
        return qs.filter(query)

    def filter_created_after(self, qs, name, value):
        date = parse_date_param(value, 'created_after')
        return qs.filter(created_at__date__gte=date)

    def filter_created_before(self, qs, name, value):
        date = parse_date_param(value, 'created_before')
        return qs.filter(created_at__date__lte=date)
