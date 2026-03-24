# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from rest_framework.exceptions import ValidationError

from bublik.core.analytics.services import AnalyticsService


class Command(BaseCommand):
    help = (
        'Prune analytics events by retention and/or max row count. '
        'This command requires ANALYTICS_ENABLED=True.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-events',
            type=int,
            default=None,
            help='Maximum number of analytics events to keep',
        )
        parser.add_argument(
            '--retention-days',
            type=int,
            default=None,
            help='Keep only events that are newer than this many days',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=10000,
            help='How many rows to delete per batch',
        )

    def handle(self, *args, **options):
        if not settings.ANALYTICS_ENABLED:
            self.stdout.write(self.style.WARNING('Analytics is disabled. Nothing to prune.'))
            return

        if not apps.is_installed('bublik.analytics'):
            msg = 'Analytics app is not installed. Check ANALYTICS_ENABLED and INSTALLED_APPS.'
            raise CommandError(msg)

        max_events = options['max_events']
        retention_days = options['retention_days']
        batch_size = options['batch_size']

        if max_events is None and retention_days is None:
            msg = 'Provide at least one of --max-events or --retention-days.'
            raise CommandError(msg)

        try:
            result = AnalyticsService.prune_events(
                max_events=max_events,
                retention_days=retention_days,
                batch_size=batch_size,
            )
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Analytics prune completed: "
                f"deleted_by_age={result['deleted_by_age']}, "
                f"deleted_by_cap={result['deleted_by_cap']}, "
                f"remaining={result['remaining']}",
            ),
        )
