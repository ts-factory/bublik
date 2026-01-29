# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError

from bublik.core.pagination_helpers import PaginatedResult
from bublik.core.run.compromised import (
    is_run_compromised,
    mark_run_compromised,
    unmark_run_compromised,
    validate_compromised_request,
)
from bublik.core.run.external_links import get_sources
from bublik.core.run.filter_expression import filter_by_expression
from bublik.core.run.stats import (
    generate_all_run_details,
    generate_runs_details,
    get_nok_results_distribution,
    get_run_stats_detailed_with_comments,
    get_run_status,
)
from bublik.core.shortcuts import serialize
from bublik.data import models
from bublik.data.serializers import RunCommentSerializer


class RunService:
    @staticmethod
    def get_run(run_id: int) -> models.TestIterationResult:
        '''Get a run by ID.

        Args:
            run_id: The ID of the test run

        Returns:
            TestIterationResult instance

        Raises:
            ValidationError: if run not found
        '''
        try:
            return models.TestIterationResult.objects.get(id=run_id)
        except models.TestIterationResult.DoesNotExist as e:
            msg = f'Run {run_id} not found'
            raise ValidationError(msg) from e

    @staticmethod
    def get_run_details(run_id: int) -> dict:
        '''Get full details for a single run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with full run details
        '''
        run = RunService.get_run(run_id)
        return generate_all_run_details(run)

    @staticmethod
    def get_run_status(run_id: int) -> str:
        '''Get status string for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Status string for the run
        '''
        run = RunService.get_run(run_id)
        return get_run_status(run)

    @staticmethod
    def get_run_stats(run_id: int, requirements: str | None = None) -> dict:
        '''Get statistics for a run.

        Args:
            run_id: The ID of the test run
            requirements: Optional requirements filter

        Returns:
            Dictionary with run statistics
        '''
        return get_run_stats_detailed_with_comments(run_id, requirements)

    @staticmethod
    def get_run_source(run_id: int) -> str:
        '''Get source URL for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Source URL string
        '''
        run = RunService.get_run(run_id)
        return get_sources(run)

    @staticmethod
    def get_run_compromised(run_id: int) -> dict:
        '''Get compromised status for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with compromised status data
        '''
        run = RunService.get_run(run_id)
        compromised_data = is_run_compromised(run)
        if not compromised_data:
            compromised_data = {'compromised': False}
        return compromised_data

    @staticmethod
    def mark_run_compromised(
        run_id: int,
        comment: str,
        bug_id: str | None = None,
        reference_key: str | None = None,
    ) -> dict:
        """Mark a run as compromised.

        Args:
            run_id: The ID of the test run
            comment: Comment explaining why it's compromised
            bug_id: Optional bug ID reference
            reference_key: Optional reference key

        Returns:
            Dictionary with comment and bug info

        Raises:
            ValidationError: if validation fails
        """
        err_msg = validate_compromised_request(run_id, comment, bug_id, reference_key)
        if err_msg:
            raise ValidationError(err_msg)

        mark_run_compromised(run_id, comment, bug_id, reference_key)
        return {
            'comment': comment,
            'bug': f'Bug ID: {bug_id}' if bug_id else None,
        }

    @staticmethod
    def unmark_run_compromised(run_id: int) -> dict:
        '''Remove compromised status from a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with success message
        '''
        unmark_run_compromised(run_id)
        return {'message': f'Run {run_id} is no longer compromised'}

    @staticmethod
    def list_runs_queryset(  # noqa: PLR0913
        start_date: str | None = None,
        finish_date: str | None = None,
        project_id: int | None = None,
        run_status: str | None = None,
        run_data: str | None = None,
        tag_expr: str | None = None,
        label_expr: str | None = None,
        revision_expr: str | None = None,
        branch_expr: str | None = None,
    ):
        '''Get filtered runs queryset.

        This provides a single source of truth for run filtering,
        shared between REST API and MCP tools.

        Args:
            start_date: Optional start date filter
            finish_date: Optional finish date filter
            project_id: Optional project ID filter
            run_status: Optional run status filter
            run_data: Comma-separated metadata (tags, labels, revisions, branches)
            tag_expr: Tag expression filter
            label_expr: Label expression filter
            revision_expr: Revision expression filter
            branch_expr: Branch expression filter

        Returns:
            QuerySet of filtered TestIterationResult objects
        '''

        queryset = models.TestIterationResult.objects.filter(test_run__isnull=True)

        # Date filters
        if start_date:
            queryset = queryset.filter(start__date__gte=start_date)
        if finish_date:
            queryset = queryset.filter(finish__date__lte=finish_date)

        # Project filter
        if project_id:
            queryset = queryset.filter(project_id=project_id)

        # Status filter
        if run_status:
            queryset = queryset.filter_by_run_status(run_status, project_id)

        # Metadata filters
        meta_types = ['tag', 'label', 'revision', 'branch']
        if run_data:
            run_data_list = run_data.split(settings.QUERY_DELIMITER)
            queryset = queryset.filter_by_run_metas(metas=run_data_list, meta_types=meta_types)

        # Expression filters
        for expr_type, expr_str in [
            ('tag', tag_expr),
            ('label', label_expr),
            ('revision', revision_expr),
            ('branch', branch_expr),
        ]:
            if expr_str:
                queryset = filter_by_expression(
                    filtered_qs=queryset,
                    expr_str=expr_str,
                    expr_type=expr_type,
                )

        return queryset.select_related('project').order_by('-start')

    @staticmethod
    def list_runs(
        start_date: str | None = None,
        finish_date: str | None = None,
        project_id: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict:
        '''List runs filtered by date range and optionally by project.

        Args:
            start_date: Optional start date (yyyy-mm-dd)
            finish_date: Optional finish date (yyyy-mm-dd)
            project_id: Optional project ID to filter by
            page: Page number (default: 1)
            page_size: Items per page (default: 25, max: 10000)

        Returns:
            Dictionary with pagination metadata and run detail dictionaries
        '''

        runs = RunService.list_runs_queryset(
            start_date=start_date,
            finish_date=finish_date,
            project_id=project_id,
        )
        runs_details = generate_runs_details(runs)
        return PaginatedResult.paginate_queryset(runs_details, page, page_size)

    @staticmethod
    def get_run_requirements(run_id: int) -> list[str]:
        '''Get requirements for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Sorted list of requirement strings
        '''
        run = RunService.get_run(run_id)
        return sorted(
            models.Meta.objects.filter(type='requirement', metaresult__result__test_run=run)
            .values_list('value', flat=True)
            .distinct(),
        )

    @staticmethod
    def get_nok_distribution(run_id: int) -> dict:
        '''Get NOK (failure) distribution for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Dictionary with NOK distribution data
        '''
        run = RunService.get_run(run_id)
        return get_nok_results_distribution(run)

    @staticmethod
    def get_run_comment(run_id: int) -> str | None:
        '''Get comment for a run.

        Args:
            run_id: The ID of the test run

        Returns:
            Comment string or None if no comment exists

        Raises:
            ValidationError: if multiple comments found
        '''
        run = RunService.get_run(run_id)
        comments = models.MetaResult.objects.filter(meta__type='comment', result=run)

        if not comments.exists():
            return None

        if comments.count() > 1:
            msg = 'Multiple comments found for the run'
            raise ValidationError(msg)

        return comments.first().meta.value

    @staticmethod
    def create_run_comment(run_id: int, content: str) -> dict:
        '''Create or update run comment.

        Args:
            run_id: The ID of the test run
            content: Comment content

        Returns:
            Dictionary with comment details
        '''
        run = RunService.get_run(run_id)

        # Check if comment already exists
        existing_comments = models.MetaResult.objects.filter(meta__type='comment', result=run)
        if existing_comments.exists():
            # Delete existing comments
            existing_comments.delete()

        # Create new comment
        mr_serializer = serialize(
            RunCommentSerializer,
            data={'comment': content},
            context={'run': run},
        )
        mr, _ = mr_serializer.get_or_create()

        return {
            'id': mr.id,
            'comment': mr.meta.value,
            'created': mr.meta.created,
        }

    @staticmethod
    def delete_run_comment(run_id: int) -> None:
        '''Delete run comment.

        Args:
            run_id: The ID of the test run

        Raises:
            ValidationError: if no comment exists
        '''
        run = RunService.get_run(run_id)
        comments = models.MetaResult.objects.filter(meta__type='comment', result=run)

        if not comments.exists():
            msg = f'No comment found for run {run_id}'
            raise ValidationError(msg)

        comments.delete()
