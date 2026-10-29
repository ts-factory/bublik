# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from bublik.core.cache import RunCache
from bublik.core.classification import parse_issue_ids
from bublik.core.exceptions import NotFoundError
from bublik.core.issue import IssueManagementService, IssueRuleManagementService
from bublik.core.measurement.services import (
    get_measurement_charts,
    get_measurement_results,
)
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.queries import get_or_none
from bublik.core.result.dto import (
    ClassifyDTO,
    ClassifyOptionsDTO,
    ClassifyTestDTO,
    UnclassifyDTO,
)
from bublik.core.run.data import (
    get_parameters_by_iterations,
    get_tags_by_runs,
    get_verdicts,
)
from bublik.core.run.stats import (
    generate_result_details,
    generate_results_details,
)
from bublik.core.run.tests_organization import build_test_paths, get_test_ids_by_name
from bublik.core.utils import get_difference, key_value_transforming
from bublik.data import models


# Marks a disposition that was not given: the category's own applies.
UNSET = object()


def _resolve_issue(issue_ref, result, actor):
    """The issue an ID names, or the one the given fields create in the result's project."""
    if isinstance(issue_ref, int) or (isinstance(issue_ref, str) and str(issue_ref).isdigit()):
        issue = models.Issue.objects.filter(pk=int(issue_ref)).first()
        if issue is None:
            raise ValidationError({'issue': f'Issue #{int(issue_ref)} not found.'})
        if issue.project_id != result.project_id:
            raise ValidationError(
                {'issue': f'Issue #{issue.id} belongs to a different project.'},
            )
        return issue
    try:
        serializer = IssueManagementService.validate(
            {**issue_ref, 'project': result.project_id}
        )
    except ValidationError as exc:
        raise ValidationError({'issue': exc.detail}) from None
    return IssueManagementService.create(serializer, actor)


def _get_or_create_rule(test_id, issue, category, matcher_data, *, disposition, active, actor):
    rule_data = {
        'issue': issue.id,
        'test': test_id,
        'category': category,
        **matcher_data,
        **disposition,
    }

    # The matcher has been checked against the results it classifies,
    # which are in the database, so it isn't looked for there again.
    rule_serializer = IssueRuleManagementService.validate(rule_data, matcher_checked=True)

    # Repeating a classification reuses the identical rule (same matcher,
    # category, disposition and active state) instead of piling up copies;
    # the result just gets one more stamp on it.
    fields = rule_serializer.validated_data
    rule = (
        models.IssueRule.objects.filter(
            issue=fields['issue'],
            test=fields['test'],
            category=fields['category'],
            expected=fields['expected'],
            parameters=fields['parameters'],
            tags=fields['tags'],
            active=active,
            # Ignored verdicts are stored as NULL, which `verdicts=None`
            # would not find: on a JSON field it looks for a JSON null.
            **(
                {'verdicts__isnull': True}
                if fields['verdicts'] is None
                else {'verdicts': fields['verdicts']}
            ),
        )
        .order_by('id')
        .first()
    )
    if rule is not None:
        return rule

    # A one-off rule only carries the stamp and never applies to imports,
    # so it is created already deactivated.
    deactivation = {} if active else {'deactivated_at': timezone.now(), 'deactivated_by': actor}
    return IssueRuleManagementService.create(
        rule_serializer, actor, active=active, **deactivation
    )


class ResultService:
    @staticmethod
    def get_result(result_id: int) -> models.TestIterationResult:
        """
        Get a result by ID.

        Args:
            result_id: The ID of the test result

        Returns:
            TestIterationResult instance

        Raises:
            NotFoundError: if result not found
        """
        try:
            return models.TestIterationResult.objects.get(id=result_id)
        except ObjectDoesNotExist as e:
            msg = f'Result {result_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def get_result_details(result_id: int) -> dict:
        """
        Get full details for a single result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with full result details
        """

        result = ResultService.get_result(result_id)

        return generate_result_details(result)

    @staticmethod
    def get_result_measurements(result_id: int) -> dict:
        """
        Get measurements for a result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with run_id, iteration_id, charts, and tables
        """
        result = ResultService.get_result(result_id)

        # Get tables
        mmrs = get_measurement_results([result_id])
        tables = [mmr.representation(additional='measurement') for mmr in mmrs]

        return {
            'run_id': result.test_run_id,
            'iteration_id': result.iteration_id,
            'charts': get_measurement_charts(result_id),
            'tables': tables,
        }

    @staticmethod
    def get_result_artifacts_and_verdicts(result_id: int) -> dict:
        """
        Get artifacts and verdicts for a result.

        Args:
            result_id: The ID of the test result

        Returns:
            Dictionary with artifacts and verdicts lists
        """
        ResultService.get_result(result_id)
        result_metas = models.Meta.objects.filter(metaresult__result__id=result_id)
        return {
            'artifacts': list(result_metas.filter(type='artifact').values()),
            'verdicts': list(result_metas.filter(type='verdict').values()),
        }

    @staticmethod
    def get_results_by_ids(result_ids: list[int]) -> dict[int, models.TestIterationResult]:
        """
        Get results by IDs, with their tests and runs.

        Args:
            result_ids: The IDs of the results

        Returns:
            Dictionary of the results found, by ID; the results that do not
            exist are not in it
        """
        return {
            result.id: result
            for result in models.TestIterationResult.objects.filter(
                pk__in=result_ids,
            ).select_related('iteration__test', 'test_run')
        }

    @staticmethod
    def validate_results_for_classification(
        result_ids: list[int],
        found: dict[int, models.TestIterationResult],
    ) -> list[models.TestIterationResult]:
        """
        Check that results to classify all exist, are results of tests and
        are of one run.

        Args:
            result_ids: The IDs of the results asked for
            found: The results found by these IDs (see get_results_by_ids)

        Returns:
            The results, in the order of the IDs

        Raises:
            ValidationError: if any of the checks fails
        """
        missing = [result_id for result_id in result_ids if result_id not in found]
        if missing:
            raise ValidationError({'result_ids': f'Results not found: {missing}.'})
        test_type = models.ResultType.conv('test')
        not_tests = [
            result.id
            for result in found.values()
            if result.iteration_id is None
            or result.test_run_id is None
            or result.iteration.test.result_type != test_type
        ]
        if not_tests:
            msg = f'Only results of tests can be classified: {not_tests}.'
            raise ValidationError({'result_ids': msg})
        if len({result.test_run_id for result in found.values()}) > 1:
            msg = 'Only results of one run can be classified at a time.'
            raise ValidationError({'result_ids': msg})
        return [found[result_id] for result_id in result_ids]

    @staticmethod
    def get_classify_options(results: list[models.TestIterationResult]) -> ClassifyOptionsDTO:
        """
        Group results by test and work out what a rule classifying each group
        may contain: the parameters all its results have, all the different
        verdict lists they have, and the important and relevant tags of their
        run (the important ones are what a rule usually contains).

        Args:
            results: The results of tests of one run

        Returns:
            ClassifyOptionsDTO with the tags of the run and a ClassifyTestDTO
            per test
        """
        run = results[0].test_run
        important_tags_by_runs, relevant_tags_by_runs = get_tags_by_runs([run])
        important_tags = important_tags_by_runs.get(run.id, [])
        relevant_tags = relevant_tags_by_runs.get(run.id, [])
        options = ClassifyOptionsDTO(
            run_id=run.id,
            tags={
                'default': sorted(important_tags),
                'allowed': sorted({*important_tags, *relevant_tags}),
            },
        )

        results_by_tests: dict[int, list[models.TestIterationResult]] = {}
        for result in results:
            results_by_tests.setdefault(result.iteration.test_id, []).append(result)
        test_paths = build_test_paths(list(results_by_tests))
        parameters_by_iterations = get_parameters_by_iterations(
            [result.iteration_id for result in results],
        )
        verdicts_by_results = get_verdicts(results)

        for test_id, test_results in results_by_tests.items():
            parameters = [
                parameters_by_iterations.get(r.iteration_id, {}) for r in test_results
            ]
            result_verdicts = {
                r.id: list(verdicts_by_results.get(r.id, ())) for r in test_results
            }
            # Verdicts are matched exactly, in order, so the results are matched
            # by a rule per verdict list they have.
            verdict_lists = []
            for verdicts in result_verdicts.values():
                if verdicts not in verdict_lists:
                    verdict_lists.append(verdicts)
            options.tests.append(
                ClassifyTestDTO(
                    test_id=test_id,
                    test_name=test_results[0].iteration.test.name,
                    test_path=test_paths.get(test_id, ''),
                    results=test_results,
                    parameters={
                        name: value
                        for name, value in parameters[0].items()
                        if all(other.get(name) == value for other in parameters[1:])
                    },
                    verdicts=verdict_lists,
                    result_verdicts=result_verdicts,
                ),
            )
        return options

    @staticmethod
    def validate_classify_matchers(options: ClassifyOptionsDTO, matchers: list[dict]) -> None:
        """
        Check that the matchers given for the rules match every result: there
        is one for each test among the results, it takes parameters only from
        those all the results of its test have and tags only from those of the
        run, and its verdicts are either ignored (None) or all the different
        verdict lists of the results, for a rule per list. What is empty or
        ignored does not restrict the rule, so it is always fine.

        Args:
            options: What the results allow a rule to contain
            matchers: The matchers given in the request, one per test

        Raises:
            ValidationError: listing what is missing or not allowed
        """
        tests = {test.test_id: test for test in options.tests}
        given = {matcher['test_id']: matcher for matcher in matchers}
        errors: list[str] = []

        unknown = sorted(set(given) - set(tests))
        if unknown:
            errors.append(f'Matchers for tests not among the results: {unknown}.')
        missing = [test.test_path for test_id, test in tests.items() if test_id not in given]
        if missing:
            errors.append(f'No matcher for the tests: {", ".join(missing)}.')

        for test_id, matcher in given.items():
            test = tests.get(test_id)
            if test is None:
                continue
            extra_tags = sorted(set(matcher['tags']) - set(options.tags['allowed']))
            if extra_tags:
                errors.append(
                    f'{test.test_path}: tags not among the important and relevant tags '
                    f'of the run: {", ".join(extra_tags)}.',
                )
            extra_parameters = sorted(
                key_value_transforming(name, value)
                for name, value in matcher['parameters'].items()
                if test.parameters.get(name) != value
            )
            if extra_parameters:
                errors.append(
                    f'{test.test_path}: parameters not shared by all its results: '
                    f'{", ".join(extra_parameters)}.',
                )
            given_verdicts = matcher['verdicts']
            if given_verdicts is not None and (
                len(given_verdicts) != len(test.verdicts)
                or any(verdicts not in given_verdicts for verdicts in test.verdicts)
            ):
                shown = '; '.join(
                    f'[{", ".join(verdicts)}]' if verdicts else '[]'
                    for verdicts in test.verdicts
                )
                errors.append(
                    f'{test.test_path}: verdicts must be null or the verdict lists of its '
                    f'results, each once: {shown}.',
                )

        if errors:
            raise ValidationError({'matchers': errors})

    @staticmethod
    def get_rule_results_by_ids(rule_result_ids: list[int]) -> dict[int, models.RuleResult]:
        """
        Get RuleResult stamps by their IDs, with what is needed to judge them.

        Args:
            rule_result_ids: The IDs of the stamps

        Returns:
            Dict mapping a stamp ID to the stamp, for the stamps that exist
        """
        return {
            rule_result.id: rule_result
            for rule_result in models.RuleResult.objects.filter(
                pk__in=rule_result_ids,
            ).select_related('result__test_run', 'issue_rule__issue')
        }

    @staticmethod
    def classify(
        results: list[models.TestIterationResult],
        *,
        issue,
        categories: list[str],
        matchers: list[dict],
        scope: str = 'future',
        expected=UNSET,
        actor=None,
    ) -> ClassifyDTO:
        """
        Classify results of one run under an issue, with a rule per test
        among them and per category, matching exactly these results, and
        stamp them with it. Repeating a classification reuses the identical
        rule, so a result gets one more stamp at most.

        Args:
            results: The results, checked by validate_results_for_classification
            issue: An existing issue's ID, or the fields of a new issue to
                create in the results' project
            categories: The categories to classify under, a rule for each
            matchers: What the rules contain, one per test (see
                validate_classify_matchers)
            scope: 'future' for rules that also apply to later imports, or
                'oneoff' for rules that only carry these stamps
            expected: Whether the results are expected, or the category's
                own disposition when not given
            actor: The user classifying, recorded on the issue, rules and stamps

        Returns:
            ClassifyDTO with the issue and the rules the results are stamped with

        Raises:
            ValidationError: if a matcher does not fit the results, the issue
                is unknown or of another project, or a new issue's fields are
                invalid
        """
        options = ResultService.get_classify_options(results)
        ResultService.validate_classify_matchers(options, matchers)
        matchers_by_test = {matcher['test_id']: matcher for matcher in matchers}
        active = scope == 'future'
        origin = (
            models.RuleResultOrigin.MANUAL_PERSISTENT
            if active
            else models.RuleResultOrigin.MANUAL_ONEOFF
        )
        disposition = {} if expected is UNSET else {'expected': expected}

        with transaction.atomic():
            issue = _resolve_issue(issue, results[0], actor)

            # A rule belongs to one test, so each test among the results gets
            # a rule per category: one matching all of that test's results if
            # the verdicts are ignored, else one per verdict list, matching the
            # results with it.
            rule_ids = []
            for test in options.tests:
                matcher = matchers_by_test[test.test_id]
                verdict_lists = [None] if matcher['verdicts'] is None else matcher['verdicts']
                for verdicts in verdict_lists:
                    matcher_data = {
                        'parameters': matcher['parameters'],
                        # Matched in order, so kept as given.
                        'verdicts': verdicts,
                        'tags': sorted(matcher['tags']),
                    }
                    for category in categories:
                        rule = _get_or_create_rule(
                            test.test_id,
                            issue,
                            category,
                            matcher_data,
                            disposition=disposition,
                            active=active,
                            actor=actor,
                        )
                        # A result already stamped with the rule keeps its stamp.
                        models.RuleResult.objects.bulk_create(
                            [
                                models.RuleResult(
                                    result=result,
                                    issue_rule=rule,
                                    origin=origin,
                                    created_by=actor,
                                )
                                for result in test.results_with(verdicts)
                            ],
                            ignore_conflicts=True,
                        )
                        rule_ids.append(rule.id)

        RunCache.delete_data_for_obj(
            results[0].test_run,
            data_keys=RunCache.KEYS_CLASSIFICATION_AFFECTED,
        )
        return ClassifyDTO(issue_id=issue.id, rule_ids=rule_ids)

    @staticmethod
    def unclassify_results(
        rule_result_ids: list[int],
        found: dict[int, models.RuleResult],
    ) -> UnclassifyDTO:
        """
        Take classification off results: delete the given manual RuleResult
        stamps (those of one-off and persistent classification, and of
        applying the rules on demand). The rules left without any stamp are
        deleted, and then the issues left without rules. Only what this call
        has left without stamps or rules is deleted, so nothing else goes
        with it, and the rules that stay are left as they are, active or not.
        The caches of the runs the stamps were in are dropped.

        Args:
            rule_result_ids: The IDs of the stamps to take off
            found: The stamps found by these IDs (see get_rule_results_by_ids)

        Returns:
            UnclassifyDTO telling what was deleted

        Raises:
            ValidationError: if any stamp is not found, or comes from import,
                which is recomputed on every import, so there is nothing to
                take off
        """
        missing = [rr_id for rr_id in rule_result_ids if rr_id not in found]
        if missing:
            raise ValidationError({'rule_result_ids': f'Stamps not found: {missing}.'})
        imported = [
            rr_id
            for rr_id in rule_result_ids
            if found[rr_id].origin == models.RuleResultOrigin.IMPORT
        ]
        if imported:
            msg = (
                f'Classification by import cannot be taken off: {imported}. '
                'Delete the rule to take that off.'
            )
            raise ValidationError({'rule_result_ids': msg})

        outcome = UnclassifyDTO()
        rules = {
            found[rr_id].issue_rule_id: found[rr_id].issue_rule for rr_id in rule_result_ids
        }
        with transaction.atomic():
            outcome.deleted_rule_result_ids = sorted(rule_result_ids)
            models.RuleResult.objects.filter(id__in=rule_result_ids).delete()

            orphaned = models.IssueRule.objects.filter(
                ~Exists(models.RuleResult.objects.filter(issue_rule=OuterRef('pk'))),
                id__in=rules,
            )
            outcome.deleted_rule_ids = sorted(orphaned.values_list('id', flat=True))
            orphaned.delete()

            emptied = {rules[rule_id].issue_id for rule_id in outcome.deleted_rule_ids}
            outcome.deleted_issue_ids = sorted(
                issue_id
                for issue_id in emptied
                if not models.IssueRule.objects.filter(issue_id=issue_id).exists()
            )
            models.Issue.objects.filter(id__in=outcome.deleted_issue_ids).delete()

        runs = {
            rule_result.result.test_run_id: rule_result.result.test_run
            for rule_result in found.values()
        }
        for run in runs.values():
            RunCache.delete_data_for_obj(run, data_keys=RunCache.KEYS_CLASSIFICATION_AFFECTED)
        return outcome

    @staticmethod
    def list_results(  # noqa: C901
        parent_id: int | None = None,
        test_name: str | None = None,
        start_exec_seqno: str | None = None,
        results: str | None = None,
        result_properties: str | None = None,
        requirements: str | None = None,
        issue: str | None = None,
    ):
        """
        List results with filtering.

        Args:
            parent_id: Filter by parent package ID
            test_name: Filter by test name
            start_exec_seqno: Position in the global run execution sequence to start
                from. Ensures that when the same test is executed multiple times within
                one parent package, only iterations of the intended execution are returned
            results: Comma-separated result statuses
            result_properties: Comma-separated result properties
            requirements: Comma-separated requirement names
            issue: Semicolon-separated issue IDs

        Returns:
            QuerySet of filtered TestIterationResult objects

        Raises:
            ValidationError: if validation fails
        """
        queries = Q()
        queryset = models.TestIterationResult.objects.filter()
        query_delimiter = settings.QUERY_DELIMITER
        errors = []

        # parent_id filtering
        if parent_id:
            parent_obj = get_or_none(models.TestIterationResult.objects, id=parent_id)
            if not parent_obj:
                errors.append('No test iteration result found by the given parent id')
            queries &= Q(parent_package=parent_id)

        # test_name filtering
        if test_name:
            test_ids = get_test_ids_by_name(test_name)
            if not test_ids:
                errors.append('No tests found by the given test name')
            queries &= Q(iteration__test__in=test_ids, iteration__hash__isnull=False)

        if errors:
            raise ValidationError(errors)

        queryset = queryset.filter(queries)

        # Retain only results consecutive relative to the global run sequence,
        # starting from the specified execution number.
        # A gap is detected when a foreign exec_seqno (belonging to a different test)
        # appears between two local ones.
        if start_exec_seqno:
            start_exec_seqno = int(start_exec_seqno)

            local_seqnos = queryset.values('exec_seqno').distinct()

            first_foreign = (
                models.TestIterationResult.objects.filter(
                    test_run=parent_obj.test_run,
                    exec_seqno__gte=start_exec_seqno,
                )
                .exclude(exec_seqno__in=local_seqnos)
                .order_by('exec_seqno')
                .values('exec_seqno')
                .first()
            )

            queryset = queryset.filter(exec_seqno__gte=start_exec_seqno)
            if first_foreign is not None:
                queryset = queryset.filter(exec_seqno__lt=first_foreign['exec_seqno'])

        # results/status filtering
        if results:
            results_list = results.split(query_delimiter)
            diff = get_difference(results_list, models.ResultStatus.all_statuses())
            if diff:
                errors.append(f'Unknown result results: {diff}')
            queryset = queryset.filter(
                meta_results__meta__type='result',
                meta_results__meta__value__in=results_list,
            )

        if errors:
            raise ValidationError(errors)

        # result_properties filtering
        if result_properties:
            queryset = queryset.filter_by_result_classification(
                result_properties.split(query_delimiter),
            )

        # requirements filtering
        if requirements:
            requirements_list = requirements.split(query_delimiter)
            available_req_metas = []
            for requirement in requirements_list:
                try:
                    available_req_metas.append(
                        models.Meta.objects.get(type='requirement', value=requirement),
                    )
                except ObjectDoesNotExist:
                    return models.TestIterationResult.objects.none()
            for req_meta in available_req_metas:
                queryset = queryset.filter(meta_results__meta=req_meta)

        # issue filtering
        if issue:
            queryset = queryset.stamped_under(parse_issue_ids(issue))

        return queryset.order_by('-start', 'id').distinct('id', 'start')

    @staticmethod
    def list_results_paginated(  # noqa: PLR0913, PLR0917
        parent_id: int | None = None,
        test_name: str | None = None,
        start_exec_seqno: str | None = None,
        results: str | None = None,
        result_properties: str | None = None,
        requirements: str | None = None,
        issue: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        """
        List results with filtering and pagination.

        Args:
            parent_id: Filter by parent package ID
            test_name: Filter by test name
            start_exec_seqno: Position in the global run execution sequence to start
                from. Ensures that when the same test is executed multiple times within
                one parent package, only iterations of the intended execution are returned
            results: Comma-separated result statuses
            result_properties: Comma-separated result properties
            requirements: Comma-separated requirement names
            issue: Semicolon-separated issue IDs
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and result details
        """
        queryset = ResultService.list_results(
            parent_id=parent_id,
            test_name=test_name,
            start_exec_seqno=start_exec_seqno,
            results=results,
            result_properties=result_properties,
            requirements=requirements,
            issue=issue,
        )

        results_details = generate_results_details(queryset)
        return PaginatedResult.paginate_queryset(results_details, page, page_size)
