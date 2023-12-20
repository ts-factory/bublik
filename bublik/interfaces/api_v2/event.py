# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import re
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.datetime_formatting import date_str_to_db
from bublik.data.models import EventLog
from bublik.data.serializers import EventLogSerializer


__all__ = [
    'EventLogViewSet',
]


class EventLogViewSet(ListModelMixin, GenericViewSet):
    serializer_class = EventLogSerializer

    def get_queryset(self):
        date = self.request.query_params.get('date', '')
        facility = self.request.query_params.get('facility', '')
        severity = self.request.query_params.get('severity', '')
        msg = self.request.query_params.get('msg', '')
        task_id = self.request.query_params.get('task_id', '')
        url = self.request.query_params.get('url', '')

        events = EventLog.objects.all()

        if facility:
            events = events.filter(facility=facility)

        if date:
            events = events.filter(timestamp__date=date_str_to_db(date))

        if severity:
            events = events.filter(severity=severity)

        if msg:
            events = events.filter(msg__contains=msg)

        if task_id and uuid.UUID(task_id):
            events = events.filter(msg__contains=task_id)

        if url:
            URLValidator()(url)
            events = events.filter(msg__contains=url)

        return events.values()

    def list(self, request, *args, **kwargs):
        try:
            importrun_events = self.get_queryset()
        except (ValueError, ValidationError) as e:
            return Response({'error': str(e)})

        import_statuses = []

        for event in importrun_events:
            event_msg = event.get('msg')
            event_facility = event.get('facility')
            event_id = event.get('id')
            event_severity = event.get('severity')
            event_timestamp = event.get('timestamp')

            msg_uri = re.search(r'(?P<url>https?://[^\s]+)', event_msg)
            found_task_id = re.search(r'-- Celery task ID (\S*)', event_msg)
            event_runtime = re.search(r'-- runtime: (\d.*) sec', event_msg)

            task_id = found_task_id.group(1) if found_task_id else None
            event_uri = msg_uri.group('url') if msg_uri else 'No URI'
            runtime = event_runtime.group(1) if event_runtime else None

            if 'failed import' in event_msg:
                event_error = re.search(r'-- Error: (.*)', event_msg)
                error_msg = event_error.group(1) if event_error else None
                import_statuses.append(
                    {
                        'event_id': event_id,
                        'facility': event_facility,
                        'severity': event_severity,
                        'uri': event_uri,
                        'celery_task': task_id,
                        'status': 'FAILURE',
                        'timestamp': event_timestamp,
                        'error_msg': error_msg,
                        'runtime': runtime,
                    },
                )
            elif 'successful import' in event_msg:
                import_statuses.append(
                    {
                        'event_id': event_id,
                        'facility': event_facility,
                        'severity': event_severity,
                        'uri': event_uri,
                        'celery_task': task_id,
                        'status': 'SUCCESS',
                        'timestamp': event_timestamp,
                        'error_msg': None,
                        'runtime': runtime,
                    },
                )

        return Response(import_statuses)
