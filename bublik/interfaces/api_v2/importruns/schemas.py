# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.importruns.serializers import (
    ImportLogLineSerializer,
    ImportrunsResponseSerializer,
    LiveImportErrorResponseSerializer,
    LiveImportInitResponseSerializer,
)


IMPORTRUNS_TAG = 'Import'

LIVE_IMPORT_RUN_PARAMETER = OpenApiParameter(
    name='run',
    type=int,
    location=OpenApiParameter.QUERY,
    required=True,
    description='Run ID returned when the live import session was started',
)

LIVE_IMPORT_SESSION_ERROR_RESPONSE = OpenApiResponse(
    response=LiveImportErrorResponseSerializer,
    description='Malformed JSON, invalid data or unknown live import session',
)


importruns_viewset_schema = extend_schema_view(
    source=extend_schema(
        summary='Import runs from a source',
        description="""
        Find runs at the passed URL and its nested directories and schedule
        a Celery task to import each of them. The URL must start with one of
        the logs bases URIs from the references configuration of the passed
        project or, if no project is passed, of any project.
        """,
        parameters=[
            OpenApiParameter(
                name='url',
                type=OpenApiTypes.URI,
                location=OpenApiParameter.QUERY,
                required=True,
                description='URL of a run or of a directory with runs',
            ),
            OpenApiParameter(
                name='project',
                type=int,
                location=OpenApiParameter.QUERY,
                description=(
                    'ID of the project to import runs into; '
                    'by default, it is taken from meta_data.json'
                ),
            ),
            OpenApiParameter(
                name='from',
                type=str,
                location=OpenApiParameter.QUERY,
                description=(
                    'Import only runs started on this date or later '
                    '(YYYY, YYYY-MM or YYYY-MM-DD)'
                ),
            ),
            OpenApiParameter(
                name='to',
                type=str,
                location=OpenApiParameter.QUERY,
                description=(
                    'Import only runs started and finished on this date or earlier '
                    '(YYYY, YYYY-MM or YYYY-MM-DD)'
                ),
            ),
            OpenApiParameter(
                name='force',
                type=bool,
                location=OpenApiParameter.QUERY,
                description='Reimport runs that have already been imported',
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                response=ImportrunsResponseSerializer,
                description=(
                    'Import tasks were scheduled; a path that failed to be traversed '
                    'has no task'
                ),
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid or not allowed URL, or invalid filter parameter',
            ),
        },
        tags=[IMPORTRUNS_TAG],
    ),
    log=extend_schema(
        summary='Get the log of an import task',
        description="""
        Return the log of the import task with the passed Celery task ID,
        line by line.
        """,
        parameters=[
            OpenApiParameter(
                name='task_id',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description='Celery task ID',
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=ImportLogLineSerializer(many=True),
                description='Import task log was successfully retrieved',
            ),
        },
        tags=[IMPORTRUNS_TAG],
    ),
    init=extend_schema(
        summary='Start a live import session',
        description="""
        Create a run from the passed run metadata, tags and execution plan
        and start a live import session for it (TE log streaming protocol).
        The body requires `interval` (heartbeat interval, sec), `meta_data`
        and `plan`; `tags` and `ts` are optional.
        """,
        request=OpenApiTypes.OBJECT,
        responses={
            200: OpenApiResponse(
                response=LiveImportInitResponseSerializer,
                description='Live import session was successfully started',
            ),
            400: OpenApiResponse(
                response=LiveImportErrorResponseSerializer,
                description='Malformed JSON, invalid data or the run already exists',
            ),
        },
        tags=[IMPORTRUNS_TAG],
    ),
    feed=extend_schema(
        summary='Feed events to a live import session',
        description="""
        Process the passed list of TE events (`test_start`, `test_end`,
        `artifact`) in the live import session of the passed run.
        """,
        parameters=[LIVE_IMPORT_RUN_PARAMETER],
        request={'application/json': {'type': 'array', 'items': {'type': 'object'}}},
        responses={
            204: OpenApiResponse(description='Events were successfully processed'),
            400: LIVE_IMPORT_SESSION_ERROR_RESPONSE,
            501: OpenApiResponse(
                response=LiveImportErrorResponseSerializer,
                description='Events without plan IDs are not supported',
            ),
        },
        tags=[IMPORTRUNS_TAG],
    ),
    finish=extend_schema(
        summary='Finish a live import session',
        description="""
        Finish the live import session of the passed run. The body requires
        `ts`, the finish timestamp.
        """,
        parameters=[LIVE_IMPORT_RUN_PARAMETER],
        request=OpenApiTypes.OBJECT,
        responses={
            204: OpenApiResponse(description='Live import session was successfully finished'),
            400: LIVE_IMPORT_SESSION_ERROR_RESPONSE,
        },
        tags=[IMPORTRUNS_TAG],
    ),
)
