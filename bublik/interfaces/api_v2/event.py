# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
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

        return events

    def list(self, request, *args, **kwargs):
        try:
            events = self.get_queryset()
        except (ValueError, ValidationError) as e:
            return Response({'error': str(e)})

        import_statuses = {
            'successful import': 'SUCCESS',
            'failed import': 'FAILURE',
        }
        import_events = []

        for event in events:
            event_msg = event.msg

            # check if this is an import event
            import_status = next(
                (
                    import_statuses[import_msg]
                    for import_msg in import_statuses
                    if import_msg in event_msg
                ),
                None,
            )
            if not import_status:
                continue

            event_id = event.id
            event_facility = event.facility
            event_severity = event.severity
            event_timestamp = event.timestamp

            msg_uri = re.search(r'(?P<url>https?://[^\s]+)', event_msg)
            found_task_id = re.search(r'-- Celery task ID (\S*)', event_msg)
            event_runtime = re.search(r'-- runtime: (\d.*) sec', event_msg)
            event_error = re.search(r'-- Error: (.*)', event_msg)

            task_id = found_task_id.group(1) if found_task_id else None
            event_uri = msg_uri.group('url') if msg_uri else 'No URI'
            runtime = event_runtime.group(1) if event_runtime else None
            error_msg = event_error.group(1) if event_error else None

            import_events.append(
                {
                    'event_id': event_id,
                    'facility': event_facility,
                    'severity': event_severity,
                    'uri': event_uri,
                    'celery_task': task_id,
                    'status': import_status,
                    'timestamp': event_timestamp,
                    'error_msg': error_msg,
                    'runtime': runtime,
                },
            )

        sort_params = request.query_params.getlist('sort')

        if not sort_params:
            import_events = sorted(
                import_events,
                key=lambda item: item['timestamp'],
                reverse=True,
            )
            return self.get_paginated_response(self.paginate_queryset(import_events))

        sort_fields = [
            (field, order == 'desc')
            for param in sort_params
            for field, order in [param.split(':')]
        ]

        def safe_sort_key(value, reverse):
            '''
            Generates a sorting key (priority, transformed value) based on the type of 'value'
            and the specified sorting order.
            '''
            if value is None:
                return (-1,) if reverse else (1,)
            if isinstance(value, datetime):
                timestamp = value.timestamp()
                return (0, -timestamp) if reverse else (0, timestamp)
            if isinstance(value, (int, float)):
                return (0, -value) if reverse else (0, value)
            if isinstance(value, str):
                return (
                    (0, value.lower())
                    if not reverse
                    else (0, ''.join(chr(255 - ord(c)) for c in value.lower()))
                )
            return (2, str(value))

        import_events = sorted(
            import_events,
            key=lambda item: tuple(
                safe_sort_key(item.get(field), reverse) for field, reverse in sort_fields
            ),
        )

        return self.get_paginated_response(self.paginate_queryset(import_events))
