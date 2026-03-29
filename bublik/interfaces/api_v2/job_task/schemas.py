# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.job_task.serializers import (
    JobTaskExecutionListSerializer,
    JobTaskExecutionRetrieveSerializer,
)


job_task_execution_viewset_schema = extend_schema_view(
    retrieve=extend_schema(
        summary='Get task executions by job',
        description='''
        Returns a list of task execution results for a given job ID.
        Each entry contains the task status, source URL, Celery task UUID, and linked run ID.
        ''',
        responses={
            200: OpenApiResponse(
                response=JobTaskExecutionRetrieveSerializer(many=True),
                description='Task executions for the given job were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No tasks found for the given job ID',
            ),
        },
        tags=['Job Task Execution'],
    ),
    list=extend_schema(
        summary='List task executions',
        description='''
        Returns a paginated list of task execution results with full details:
        timing information, event logs, and an extracted error message for failed tasks.
        Supports filtering by job, run, URL, Celery task UUID, and status.
        ''',
        responses={
            200: OpenApiResponse(
                response=JobTaskExecutionListSerializer(many=True),
                description='Task executions were successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='No tasks found matching the given parameters',
            ),
        },
        tags=['Job Task Execution'],
    ),
)
