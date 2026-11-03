# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Exists, OuterRef, Q
from rest_framework.exceptions import ValidationError

from bublik.core.classification import parse_issue_ids
from bublik.core.exceptions import NotFoundError
from bublik.core.measurement.services import (
    get_measurement_charts,
    get_measurement_results,
)
from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.queries import get_or_none
from bublik.core.result.dto import ClassifyRuleOptionsDTO, UnclassifyDTO
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
        are of one project (an issue and its rules belong to one project).

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
        if len({result.project_id for result in found.values()}) > 1:
            msg = 'Only results of one project can be classified at a time.'
            raise ValidationError({'result_ids': msg})
        return [found[result_id] for result_id in result_ids]

    @staticmethod
    def get_classify_options(
        results: list[models.TestIterationResult],
    ) -> list[ClassifyRuleOptionsDTO]:
        """
        Group results for rules, by test and by runs, and work out what each
        rule may contain.

        A test gets a rule for the results of the runs with the same important
        tags, which the rule usually contains. A rule matches the runs whose
        tags contain its own, so the runs whose important tags contain those
        of other runs of the test join the group of the runs with the most of
        those tags that contain no others: a rule for them would match no run
        that the rule of that group doesn't. What a rule may contain is worked
        out from its own group of results alone, so that it matches each of
        them: the parameters all of them have, all the different verdict
        lists they have, and the important and relevant tags all of its runs
        have.

        Args:
            results: The results of tests of one project

        Returns:
            A ClassifyRuleOptionsDTO per rule: test by test, in the order the
            tests first appear, and within a test in the order the runs of the
            groups first appear
        """
        run_ids = list(dict.fromkeys(result.test_run_id for result in results))
        important_tags_by_runs, relevant_tags_by_runs = get_tags_by_runs(run_ids)
        important_tags = {
            run_id: frozenset(important_tags_by_runs.get(run_id, [])) for run_id in run_ids
        }
        allowed_tags = {
            run_id: important_tags[run_id] | set(relevant_tags_by_runs.get(run_id, []))
            for run_id in run_ids
        }

        results_by_tests: dict[int, list[models.TestIterationResult]] = {}
        for result in results:
            results_by_tests.setdefault(result.iteration.test_id, []).append(result)
        test_paths = build_test_paths(list(results_by_tests))
        parameters_by_iterations = get_parameters_by_iterations(
            [result.iteration_id for result in results],
        )
        verdicts_by_results = get_verdicts(results)

        options = []
        for test_id, test_results in results_by_tests.items():
            test_run_ids = list(dict.fromkeys(result.test_run_id for result in test_results))
            groups = ResultService._group_runs_by_tags(
                {run_id: important_tags[run_id] for run_id in test_run_ids},
            )
            for group_tags, group_run_ids in groups.items():
                group_results = [r for r in test_results if r.test_run_id in group_run_ids]
                parameters = [
                    parameters_by_iterations.get(r.iteration_id, {}) for r in group_results
                ]
                result_verdicts = {
                    r.id: list(verdicts_by_results.get(r.id, ())) for r in group_results
                }
                # Verdicts are matched exactly, in order, so the results are
                # matched by a rule per verdict list they have.
                verdict_lists = []
                for verdicts in result_verdicts.values():
                    if verdicts not in verdict_lists:
                        verdict_lists.append(verdicts)
                options.append(
                    ClassifyRuleOptionsDTO(
                        test_id=test_id,
                        test_name=test_results[0].iteration.test.name,
                        test_path=test_paths.get(test_id, ''),
                        run_ids=group_run_ids,
                        results=group_results,
                        parameters={
                            name: value
                            for name, value in parameters[0].items()
                            if all(other.get(name) == value for other in parameters[1:])
                        },
                        verdicts=verdict_lists,
                        tags={
                            'default': sorted(group_tags),
                            'allowed': sorted(
                                set.intersection(
                                    *(set(allowed_tags[run_id]) for run_id in group_run_ids),
                                ),
                            ),
                        },
                        result_verdicts=result_verdicts,
                    ),
                )
        return options

    @staticmethod
    def _group_runs_by_tags(
        tags_by_runs: dict[int, frozenset[str]],
    ) -> dict[frozenset[str], list[int]]:
        """
        Group runs for rules by their tags: a group for each set of tags that
        contains no other set among them, holding the runs with that set and
        those whose tags contain it (of several such sets, the one with the
        most tags, and of those, the first).

        Args:
            tags_by_runs: The tags of each run, in the order of the runs

        Returns:
            The runs of each group, by its tags, in the order the runs of the
            groups first appear
        """
        tag_sets = list(dict.fromkeys(tags_by_runs.values()))
        smallest = [tags for tags in tag_sets if not any(other < tags for other in tag_sets)]
        groups: dict[frozenset[str], list[int]] = {}
        for run_id, tags in tags_by_runs.items():
            # max() takes the first of those with the most tags.
            group_tags = max(
                (group_tags for group_tags in smallest if group_tags <= tags),
                key=len,
            )
            groups.setdefault(group_tags, []).append(run_id)
        return groups

    @staticmethod
    def validate_classify_matchers(
        options: list[ClassifyRuleOptionsDTO],
        matchers: list[dict],
    ) -> list[tuple[ClassifyRuleOptionsDTO, dict]]:
        """
        Check that the matchers given for the rules match every result: there
        is one for each rule of get_classify_options(), told by its test and
        its runs, and it takes parameters only from those all the results of
        the rule have, tags only from those all its runs have, and its
        verdicts are either ignored (None) or all the different verdict lists
        of its results, for a rule per list. What is empty or ignored does
        not restrict the rule, so it is always fine.

        Args:
            options: What each rule classifying the results may contain
            matchers: The matchers given in the request, one per rule

        Returns:
            Each rule with its matcher, in the order of the rules

        Raises:
            ValidationError: listing what is missing or not allowed
        """
        rules = {(rule.test_id, frozenset(rule.run_ids)): rule for rule in options}
        given: dict[tuple[int, frozenset[int]], dict] = {}
        errors: list[str] = []
        for matcher in matchers:
            key = (matcher['test_id'], frozenset(matcher['run_ids']))
            if key not in rules:
                errors.append(
                    f'Matcher for test {matcher["test_id"]} and runs {matcher["run_ids"]}, '
                    'which are not those of a rule.',
                )
            elif key in given:
                errors.append(
                    f'Repeated matcher for test {matcher["test_id"]} and runs '
                    f'{matcher["run_ids"]}.',
                )
            else:
                given[key] = matcher

        matched = []
        for key, rule in rules.items():
            rule_matcher = given.get(key)
            if rule_matcher is None:
                errors.append(f'{rule.test_path}: no matcher for runs {rule.run_ids}.')
                continue
            errors.extend(
                f'{rule.test_path}, runs {rule.run_ids}: {error}'
                for error in ResultService._check_classify_matcher(rule, rule_matcher)
            )
            matched.append((rule, rule_matcher))

        if errors:
            raise ValidationError({'matchers': errors})
        return matched

    @staticmethod
    def _check_classify_matcher(rule: ClassifyRuleOptionsDTO, matcher: dict) -> list[str]:
        """
        Check that a matcher contains only what its rule allows.

        Returns:
            The problems found
        """
        errors = []
        extra_parameters = sorted(
            key_value_transforming(name, value)
            for name, value in matcher['parameters'].items()
            if rule.parameters.get(name) != value
        )
        if extra_parameters:
            errors.append(
                f'parameters not shared by all its results: {", ".join(extra_parameters)}.'
            )
        given_verdicts = matcher['verdicts']
        if given_verdicts is not None and (
            len(given_verdicts) != len(rule.verdicts)
            or any(verdicts not in given_verdicts for verdicts in rule.verdicts)
        ):
            shown = '; '.join(
                f'[{", ".join(verdicts)}]' if verdicts else '[]' for verdicts in rule.verdicts
            )
            errors.append(
                'verdicts must be null or the verdict lists of its results, each once: '
                f'{shown}.',
            )
        extra_tags = sorted(set(matcher['tags']) - set(rule.tags['allowed']))
        if extra_tags:
            errors.append(
                'tags not among the important and relevant tags of all its runs: '
                f'{", ".join(extra_tags)}.',
            )
        return errors

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
    def unclassify_results(
        rule_result_ids: list[int],
        found: dict[int, models.RuleResult],
    ) -> UnclassifyDTO:
        """
        Take classification off results: delete the given manual RuleResult
        stamps (those of one-off and persistent classification, and of
        applying the rules on demand). The rules
        left without any stamp are deleted, and then the issues left without
        rules. Only what this call has left without stamps or rules is
        deleted, so nothing else goes with it, and the rules that stay are
        left as they are, active or not.

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
