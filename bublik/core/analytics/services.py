# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from datetime import timedelta
import json
from uuid import uuid4

from django.conf import settings
from django.db.models import Count, Q, TextField
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

from bublik.analytics.models import AnalyticsEvent


class AnalyticsService:
    MAX_EVENTS_PER_BATCH = 100
    MAX_PAYLOAD_LENGTH = 8192
    FACET_VALUES_LIMIT = 100
    TOP_VALUES_LIMIT = 10
    SUPPORTED_SCHEMA_VERSION = 1

    @classmethod
    def parse_filter_values(cls, raw_value: str | None) -> list[str]:
        return cls._split_filter_values(raw_value)

    @classmethod
    def get_filtered_queryset(cls, query_params):
        queryset = AnalyticsEvent.objects.all()

        event_types = cls._split_filter_values(query_params.get('event_type'))
        if event_types:
            queryset = queryset.filter(event_type__in=event_types)

        event_names = cls._split_filter_values(query_params.get('event_name'))
        if event_names:
            queryset = queryset.filter(event_name__in=event_names)

        paths = cls._split_filter_values(query_params.get('path'))
        if paths:
            queryset = queryset.filter(path__in=paths)

        anon_ids = cls._split_filter_values(query_params.get('anon_id'))
        if anon_ids:
            queryset = queryset.filter(anon_id__in=anon_ids)

        app_versions = cls._split_filter_values(query_params.get('app_version'))
        if app_versions:
            queryset = queryset.filter(app_version__in=app_versions)

        search_query = cls._normalize_search_text(query_params.get('search'))
        payload_search_query = cls._normalize_search_text(query_params.get('payload_search'))
        if payload_search_query:
            queryset = queryset.annotate(payload_text=Cast('payload', output_field=TextField()))

        if search_query:
            search_filter = Q(event_name__icontains=search_query)
            search_filter.add(Q(path__icontains=search_query), 'OR')
            search_filter.add(Q(event_type__icontains=search_query), 'OR')
            search_filter.add(Q(anon_id__icontains=search_query), 'OR')

            queryset = queryset.filter(search_filter)

        if payload_search_query:
            queryset = queryset.filter(payload_text__icontains=payload_search_query)

        date_from = query_params.get('from')
        if date_from:
            queryset = queryset.filter(
                occurred_at__gte=cls._parse_datetime(date_from, 'from'),
            )

        date_to = query_params.get('to')
        if date_to:
            queryset = queryset.filter(
                occurred_at__lte=cls._parse_datetime(date_to, 'to'),
            )

        return queryset.order_by('-occurred_at', '-id')

    @classmethod
    def collect_events(cls, events_data):
        now = timezone.now()
        events_to_create = [cls._build_event(event_data, now) for event_data in events_data]

        AnalyticsEvent.objects.bulk_create(events_to_create, ignore_conflicts=True)
        return len(events_to_create)

    @staticmethod
    def get_overview(queryset):
        total_events = queryset.count()
        page_views = queryset.filter(event_type='page_view').count()
        unique_anonymous_users = (
            queryset.exclude(anon_id='').values('anon_id').distinct().count()
        )

        top_event_names = AnalyticsService._top_by_field(queryset, 'event_name', limit=5)
        top_paths = AnalyticsService._top_by_field(
            queryset.filter(event_type='page_view'),
            'path',
            limit=5,
        )

        return {
            'total_events': total_events,
            'page_views': page_views,
            'unique_anonymous_users': unique_anonymous_users,
            'top_event_names': top_event_names,
            'top_paths': top_paths,
        }

    @classmethod
    def get_facets(cls, queryset):
        event_type_rows = cls._top_by_field(
            queryset,
            'event_type',
            limit=cls.FACET_VALUES_LIMIT,
        )
        event_name_rows = cls._top_by_field(
            queryset.filter(event_type='event'),
            'event_name',
            limit=cls.FACET_VALUES_LIMIT,
        )
        path_rows = cls._top_by_field(
            queryset,
            'path',
            limit=cls.FACET_VALUES_LIMIT,
        )
        anon_id_rows = cls._top_by_field(
            queryset,
            'anon_id',
            limit=cls.FACET_VALUES_LIMIT,
        )
        app_version_rows = cls._top_by_field(
            queryset,
            'app_version',
            limit=cls.FACET_VALUES_LIMIT,
        )

        return {
            'event_types': [
                {'value': row['event_type'], 'count': row['count']} for row in event_type_rows
            ],
            'event_names': [
                {'value': row['event_name'], 'count': row['count']} for row in event_name_rows
            ],
            'paths': [{'value': row['path'], 'count': row['count']} for row in path_rows],
            'anon_ids': [
                {'value': row['anon_id'], 'count': row['count']} for row in anon_id_rows
            ],
            'app_versions': [
                {'value': row['app_version'], 'count': row['count']} for row in app_version_rows
            ],
        }

    @classmethod
    def resolve_top_events_path(cls, query_params) -> str:
        top_events_path = query_params.get('top_events_path', '')
        if not top_events_path:
            path_filters = cls.parse_filter_values(
                query_params.get('path'),
            )
            if path_filters:
                top_events_path = path_filters[0]

        return top_events_path

    @classmethod
    def get_charts(cls, queryset, *, top_events_path: str):
        top_paths = cls._top_by_field(
            queryset.filter(event_type='page_view'),
            'path',
            limit=cls.TOP_VALUES_LIMIT,
        )

        events_queryset = queryset.filter(event_type='event')
        if top_events_path:
            events_queryset = events_queryset.filter(path=top_events_path)

        top_events = cls._top_by_field(
            events_queryset,
            'event_name',
            limit=cls.TOP_VALUES_LIMIT,
        )

        return {
            'top_events_path': top_events_path,
            'top_paths': top_paths,
            'top_events': top_events,
        }

    @classmethod
    def get_charts_for_query(cls, queryset, query_params):
        top_events_path = cls.resolve_top_events_path(query_params)

        return cls.get_charts(
            queryset,
            top_events_path=top_events_path,
        )

    @classmethod
    def validate_schema_version(cls, schema_version: int):
        if schema_version != cls.SUPPORTED_SCHEMA_VERSION:
            msg = f'Only schema_version={cls.SUPPORTED_SCHEMA_VERSION} is supported'
            raise ValidationError({'schema_version': msg})

    @staticmethod
    def validate_payload_size(payload, max_payload_length: int):
        serialized_payload = json.dumps(payload)
        if len(serialized_payload) > max_payload_length:
            msg = f'Payload is too large (>{max_payload_length} bytes)'
            raise ValidationError(msg)
        return payload

    @staticmethod
    def prune_events(
        *,
        max_events: int | None = None,
        retention_days: int | None = None,
        batch_size: int = 10000,
    ):
        if batch_size <= 0:
            msg = 'batch_size must be greater than 0'
            raise ValidationError(msg)

        if max_events is not None and max_events < 0:
            msg = 'max_events must be greater than or equal to 0'
            raise ValidationError(msg)

        if retention_days is not None and retention_days < 0:
            msg = 'retention_days must be greater than or equal to 0'
            raise ValidationError(msg)

        deleted_by_age = 0
        deleted_by_cap = 0

        if retention_days is not None:
            cutoff = timezone.now() - timedelta(days=retention_days)

            while True:
                old_ids = list(
                    AnalyticsEvent.objects.filter(occurred_at__lt=cutoff)
                    .order_by('occurred_at', 'id')
                    .values_list('id', flat=True)[:batch_size],
                )
                if not old_ids:
                    break

                deleted, _ = AnalyticsEvent.objects.filter(id__in=old_ids).delete()
                deleted_by_age += deleted

        if max_events is not None:
            total_events = AnalyticsEvent.objects.count()

            while total_events > max_events:
                delete_count = min(batch_size, total_events - max_events)
                oldest_ids = list(
                    AnalyticsEvent.objects.order_by('occurred_at', 'id').values_list(
                        'id',
                        flat=True,
                    )[:delete_count],
                )
                if not oldest_ids:
                    break

                deleted, _ = AnalyticsEvent.objects.filter(id__in=oldest_ids).delete()
                deleted_by_cap += deleted
                total_events = AnalyticsEvent.objects.count()

        return {
            'deleted_by_age': deleted_by_age,
            'deleted_by_cap': deleted_by_cap,
            'remaining': AnalyticsEvent.objects.count(),
        }

    @staticmethod
    def _split_filter_values(raw_value: str | None) -> list[str]:
        if not raw_value:
            return []

        return [
            value.strip()
            for value in raw_value.split(settings.QUERY_DELIMITER)
            if value.strip()
        ]

    @staticmethod
    def _normalize_search_text(raw_value: str | None) -> str | None:
        if raw_value is None:
            return None

        value = raw_value.strip()
        return value or None

    @staticmethod
    def _parse_datetime(raw_value: str, field_name: str):
        parsed_value = parse_datetime(raw_value)
        if parsed_value is None:
            raise ValidationError({field_name: 'Invalid datetime format'})

        if timezone.is_naive(parsed_value):
            return timezone.make_aware(parsed_value)

        return parsed_value

    @staticmethod
    def _build_event(event_data, now):
        return AnalyticsEvent(
            event_uuid=event_data.get('event_uuid') or uuid4(),
            event_type=event_data['event_type'],
            event_name=event_data.get('event_name', ''),
            path=event_data.get('path', ''),
            anon_id=event_data.get('anon_id', ''),
            session_id=event_data.get('session_id', ''),
            browser_name=event_data.get('browser_name', ''),
            browser_version=event_data.get('browser_version', ''),
            os_name=event_data.get('os_name', ''),
            user_agent=event_data.get('user_agent', ''),
            app_version=event_data.get('app_version', ''),
            payload=event_data.get('payload', {}),
            occurred_at=event_data.get('occurred_at', now),
        )

    @staticmethod
    def _top_by_field(queryset, field_name: str, limit: int):
        rows = (
            queryset.exclude(**{field_name: ''})
            .values(field_name)
            .annotate(count=Count('id'))
            .order_by('-count')[:limit]
        )
        return list(rows)
