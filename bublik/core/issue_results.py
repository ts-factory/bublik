# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.db.models import Exists, OuterRef, Q, QuerySet, TextChoices

from bublik.core.classification import suppressed_subquery
from bublik.core.exceptions import NotFoundError
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.result.services import ResultService
from bublik.core.run.data import get_tags_by_runs
from bublik.core.run.stats import generate_results_details
from bublik.core.utils import key_value_transforming
from bublik.data.models import (
    IssueRule,
    IssueState,
    MetaResult,
    ResultStatus,
    RuleResult,
    TestArgument,
    TestIterationResult,
)


class ResultClassification(TextChoices):
    """
    The one chip a result carries for what its stamps did to it, so a filter
    on the chip selects exactly the rows that show it. It is built from the
    same `effect_for()` rule as the `effect` of each stamp in a row's
    `issues`, taking the strongest effect among them. Every result here is
    stamped, so the UI's `untriaged` chip never applies.
    """

    SUPPRESSED = 'suppressed'
    STALE = 'stale'
    UNEXPECTED = 'unexpected'
    MARKED = 'marked'
    NO_EFFECT = 'no-effect'


class ResultProperty(TextChoices):
    EXPECTED = 'expected'
    UNEXPECTED = 'unexpected'


@dataclass
class IssueResultsFilters:
    """
    What narrows an issue's results, validated by the query serializer. The
    `;`-separated strings go to the plain listing as they are. `categories`
    matches a row stamped by a rule of this issue in any of the categories,
    and `classifications` a row showing any of the chips; `verdicts`,
    `artifacts` and `parameters` match a row carrying every one of them, as
    the table's own filtering does.
    """

    results: str | None = None
    result_properties: str | None = None
    requirements: str | None = None
    categories: list[str] = field(default_factory=list)
    classifications: list[str] = field(default_factory=list)
    verdicts: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)

    @classmethod
    def from_params(cls, params: dict) -> IssueResultsFilters:
        """The filters named by the validated query parameters of the results listing."""
        result_properties = params.get('result_properties')
        return cls(
            results=params.get('results'),
            result_properties=(
                settings.QUERY_DELIMITER.join(result_properties) if result_properties else None
            ),
            requirements=params.get('requirements'),
            categories=params.get('category', []),
            classifications=params.get('classification', []),
            verdicts=params.get('verdict', []),
            artifacts=params.get('artifact', []),
            parameters=params.get('parameter', []),
        )


class IssueResultsService:
    """
    Every result stamped under one issue, across runs, narrowed and paged in
    the database rather than in the browser: an issue can stamp thousands of
    results, and the row details are costly to build, so only the page that
    is shown gets built.
    """

    MAX_PAGE_SIZE = 1000

    @staticmethod
    def annotate_classification(queryset: QuerySet) -> QuerySet:
        """
        The booleans `classification_query()` reads: whether the result
        failed, whether a stamp suppresses that, and what kinds of stamps it
        carries. A result with a suppressing stamp is `suppressed`, a pass
        otherwise is `no-effect`, and a failure takes the strongest effect
        of its stamps as `effect_for()` defines them - stale (a stamp of a
        closed issue, whatever its disposition) over unexpected over marked.
        """
        # Aliased rather than annotated: they are only filtered on, and in
        # the SELECT list Postgres computes them over the whole tables.
        stamps = RuleResult.objects.filter(result_id=OuterRef('id'))
        return queryset.alias(
            _has_err=Exists(
                MetaResult.objects.filter(result_id=OuterRef('id'), meta__type='err'),
            ),
            _suppressed=Exists(suppressed_subquery()),
            _stale=Exists(stamps.filter(issue_rule__issue__state=IssueState.CLOSED)),
            _unexpected_stamp=Exists(stamps.filter(issue_rule__expected=False)),
        )

    @staticmethod
    def classification_query(classification: str) -> Q:
        """
        The rows showing one classification chip, over the annotations
        `annotate_classification()` adds.
        """
        failed = Q(_has_err=True, _suppressed=False)
        queries = {
            ResultClassification.SUPPRESSED: Q(_suppressed=True),
            ResultClassification.NO_EFFECT: Q(_has_err=False, _suppressed=False),
            ResultClassification.STALE: failed & Q(_stale=True),
            ResultClassification.UNEXPECTED: failed & Q(_stale=False, _unexpected_stamp=True),
            ResultClassification.MARKED: failed & Q(_stale=False, _unexpected_stamp=False),
        }
        return queries[ResultClassification(classification)]

    @staticmethod
    def apply_filters(
        queryset: QuerySet,
        filters: IssueResultsFilters,
        issue_id: int,
    ) -> QuerySet:
        """
        Narrow a result queryset by the axes the plain listing lacks. Each
        chained filter joins the metas afresh, so a result must carry every
        value rather than any of them.
        """
        for verdict in filters.verdicts:
            queryset = queryset.filter(
                meta_results__meta__type='verdict',
                meta_results__meta__value=verdict,
            )
        for artifact in filters.artifacts:
            queryset = queryset.filter(
                meta_results__meta__type='artifact',
                meta_results__meta__value=artifact,
            )
        for parameter in filters.parameters:
            # A bare name is an argument with an empty value, the form the
            # rows and the facets give such an argument.
            name, _, value = parameter.partition(settings.KEY_VALUE_DELIMITER)
            queryset = queryset.filter(
                iteration__test_arguments__name=name,
                iteration__test_arguments__value=value,
            )

        if filters.categories:
            # In one filter() call, so both conditions hold for the same stamp:
            # the category of this issue's rule, not of another issue's.
            queryset = queryset.filter(
                rule_results__issue_rule__issue_id=issue_id,
                rule_results__issue_rule__category__in=filters.categories,
            )

        if filters.classifications:
            query = Q()
            for classification in filters.classifications:
                query |= IssueResultsService.classification_query(classification)
            queryset = IssueResultsService.annotate_classification(queryset).filter(query)

        return queryset

    @staticmethod
    def facets(issue_id: int) -> dict[str, list[str]]:
        """
        The values each filter can take over every result of the issue, for
        the filter bar's option lists. Over the unfiltered set, so an applied
        filter never hides the option that would widen it again.
        """
        base = TestIterationResult.objects.stamped_under([issue_id])
        result_ids = base.values('id')

        def meta_values(meta_type: str) -> list[str]:
            return list(
                MetaResult.objects.filter(result_id__in=result_ids, meta__type=meta_type)
                .order_by('meta__value')
                .values_list('meta__value', flat=True)
                .distinct(),
            )

        parameters = [
            key_value_transforming(name, value)
            for name, value in TestArgument.objects.filter(
                test_iterations__testiterationresult__id__in=result_ids,
            )
            .order_by('name', 'value')
            .values_list('name', 'value')
            .distinct()
        ]

        result_properties = [
            prop
            for prop in ResultProperty.values
            if base.filter_by_result_classification([prop]).exists()
        ]
        annotated = IssueResultsService.annotate_classification(base)
        classifications = [
            classification
            for classification in ResultClassification.values
            if annotated.filter(
                IssueResultsService.classification_query(classification),
            ).exists()
        ]

        statuses = meta_values('result')
        return {
            # In the status enum's order, as the run page lists them.
            'results': [status for status in ResultStatus.all_statuses() if status in statuses],
            'result_properties': result_properties,
            'verdicts': meta_values('verdict'),
            'categories': list(
                IssueRule.objects.filter(issue_id=issue_id, rule_results__isnull=False)
                .order_by('category')
                .values_list('category', flat=True)
                .distinct(),
            ),
            'classifications': classifications,
            'artifacts': meta_values('artifact'),
            'parameters': parameters,
            'requirements': meta_values('requirement'),
        }

    @staticmethod
    def _with_run_tags(page_queryset) -> list[dict]:
        """
        The page's rows, each carrying its run's tags. The rows span runs,
        and the history links the UI draws per row need the run's tags, so
        without them it would fetch every run's details - one request per
        run on the page.
        """
        rows = generate_results_details(page_queryset)
        run_ids = {row['run_id'] for row in rows if row['run_id'] is not None}
        important_by_run, relevant_by_run = get_tags_by_runs(list(run_ids))
        for row in rows:
            row['run_tags'] = {
                'important_tags': important_by_run.get(row['run_id'], []),
                'relevant_tags': relevant_by_run.get(row['run_id'], []),
            }
        return rows

    @staticmethod
    def page(
        issue_id: int,
        filters: IssueResultsFilters,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        """
        One page of the issue's results, newest first, as the plain result
        listing shapes them, under the same pagination block the other
        listings carry. Only this page's row details are built.

        A page past the last one is empty and carries the real count, as in
        the other listings, so that a client can step back to the last page.

        Raises:
            ValidationError: if a filter the plain listing checks is invalid
        """
        queryset = IssueResultsService.apply_filters(
            ResultService.list_results(
                results=filters.results,
                result_properties=filters.result_properties,
                requirements=filters.requirements,
                issue=str(issue_id),
            ),
            filters,
            issue_id,
        )
        try:
            paged = PaginatedResult.paginate_queryset(queryset, page, page_size)
        except NotFoundError:
            return {
                'pagination': {
                    'page': page,
                    'count': queryset.count(),
                    'next': None,
                    'previous': None,
                },
                'results': [],
            }
        paged['results'] = IssueResultsService._with_run_tags(paged['results'])
        return paged
