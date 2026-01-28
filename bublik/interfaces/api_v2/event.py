# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import groupby
import re
import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from django.db.models import Case, CharField, Q, Value, When
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.mixins import ListModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.datetime_formatting import date_str_to_db
from bublik.data.models import EventLog
from bublik.data.serializers import EventLogSerializer


__all__ = [
    'ImportEventViewSet',
]


class ImportEventViewSet(ListModelMixin, GenericViewSet):
    serializer_class = EventLogSerializer

    def get_queryset(self):
        import_status_by_keyword = {
            'received import': 'RECEIVED',
            'started import': 'STARTED',
            'successful import': 'SUCCESS',
            'failed import': 'FAILURE',
        }

        # build OR query to match any known import status keyword
        import_query = Q()
        for import_keyword in import_status_by_keyword:
            import_query |= Q(msg__icontains=import_keyword)

        # build conditional annotation
        status_cases = [
            When(msg__icontains=import_keyword, then=Value(import_status))
            for import_keyword, import_status in import_status_by_keyword.items()
        ]

        import_events = EventLog.objects.filter(import_query).annotate(
            status=Case(*status_cases, output_field=CharField()),
        )

        date = self.request.query_params.get('date', '')
        facility = self.request.query_params.get('facility', '')
        severity = self.request.query_params.get('severity', '')
        msg = self.request.query_params.get('msg', '')
        task_id = self.request.query_params.get('task_id', '')
        url = self.request.query_params.get('url', '')

        if facility:
            import_events = import_events.filter(facility=facility)

        if date:
            import_events = import_events.filter(timestamp__date=date_str_to_db(date))

        if severity:
            import_events = import_events.filter(severity=severity)

        if msg:
            import_events = import_events.filter(msg__contains=msg)

        if task_id and uuid.UUID(task_id):
            import_events = import_events.filter(msg__contains=task_id)

        if url:
            URLValidator()(url)
            import_events = import_events.filter(msg__contains=url)

        return import_events

    def list(self, request, *args, **kwargs):
        try:
            import_events = self.get_queryset()
        except ValueError as ve:
            msg = 'Invalid date or UUID format.'
            raise DRFValidationError(msg) from ve
        except DjangoValidationError as dve:
            msg = 'Invalid URL or filter parameter.'
            raise DRFValidationError(msg) from dve

        import_events_data = []

        for import_event in import_events:
            import_event_msg = import_event.msg

            def find(event_msg, pattern, cast=None):
                if m := re.search(pattern, event_msg):
                    return cast(m.group(1)) if cast else m.group(1)
                return None

            import_events_data.append(
                {
                    'event_id': import_event.id,
                    'timestamp': import_event.timestamp,
                    'facility': import_event.facility,
                    'severity': import_event.severity,
                    'status': import_event.status,
                    'uri': find(import_event_msg, r'(?P<url>https?://\S+)'),
                    'celery_task': find(import_event_msg, r'-- Celery task ID (\S+)'),
                    'error_msg': find(import_event_msg, r'--\s*Error:\s*(.*?)(?:\s*--|$)'),
                    'runtime': find(
                        import_event_msg,
                        r'-- runtime:\s*([\d.]+) sec',
                        cast=float,
                    ),
                    'run_id': find(import_event_msg, r'-- run_id=(\d+)', cast=int),
                },
            )

        # sort to ensure correct grouping by uri and
        # descending order within groups by timestamp
        import_events_data.sort(
            key=lambda e: (e['uri'] or '', e['timestamp']),
            reverse=True,
        )

        # group events by uri
        events_grouped_by_uri = []
        for uri, uri_events in groupby(
            import_events_data,
            key=lambda e: e['uri'],
        ):
            uri_events = list(uri_events)
            # if task is None, add each event as its own group
            if uri is None:
                for event in uri_events:
                    events_grouped_by_uri.append([event])
            else:
                events_grouped_by_uri.append(uri_events)

        # sort groups by timestamp of their last event (newest first)
        events_grouped_by_uri.sort(key=lambda g: g[-1]['timestamp'], reverse=True)

        # paginate the grouped events
        page = self.paginate_queryset(events_grouped_by_uri)
        if page is not None:
            return self.get_paginated_response(page)

        return Response(events_grouped_by_uri)
