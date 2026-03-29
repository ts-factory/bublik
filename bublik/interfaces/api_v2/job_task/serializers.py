# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from rest_framework import serializers

from bublik.interfaces.api_v2.eventlog.serializers import EventLogSerializer


class JobTaskExecutionListSerializer(serializers.Serializer):
    '''Serializer for a single task execution entry returned by the list endpoint.
    Includes full execution details: timing, event logs, and error message.'''

    status = serializers.CharField()
    run_source_url = serializers.CharField()
    celery_task = serializers.UUIDField()
    started_at = serializers.DateTimeField(allow_null=True)
    finished_at = serializers.DateTimeField(allow_null=True)
    runtime = serializers.DurationField(allow_null=True)
    job_id = serializers.IntegerField()
    run_id = serializers.IntegerField(allow_null=True)
    event_logs = EventLogSerializer(many=True)
    error_msg = serializers.CharField(allow_null=True)


class JobTaskExecutionRetrieveSerializer(serializers.Serializer):
    '''Serializer for a single task execution entry returned by the retrieve-by-job endpoint.
    Contains only the essential fields: status, source URL, Celery task UUID, and linked run ID.
    '''

    status = serializers.CharField()
    run_source_url = serializers.CharField()
    celery_task = serializers.UUIDField()
    run_id = serializers.IntegerField(allow_null=True)
