# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

"""
Management command: reimport_runs
Usage: python manage.py reimport_runs [-i <id> ...] [-f <date>] [-t <date>]
       [-c <criterion> ...] [--done-after <datetime>] [--max-running <n>]
       [--hours <start>-<end>] [--poll <seconds>] [--task-timeout <hours>]
       [--dry-run]

Imports runs again with force from their logs, gradually, so that other imports
are not held up: no more than --max-running imports of the runs run at a time,
and the next one is scheduled only when no other task waits in the Celery queue.
An import running longer than --task-timeout is revoked, so that it frees its
worker process for the next one. Each run is imported into the project it
belongs to, whatever project its meta_data.json names.

The runs are selected by IDs and dates, and optionally by criteria (-c), which
all have to be met. Unfinished runs are never selected. With --done-after, runs
imported successfully since then are skipped, so that the command can be
stopped and run again to go on.

The command runs until all the imports are finished, which may take days, so it
is to be run in a terminal multiplexer such as tmux.
"""

from __future__ import annotations

from argparse import ArgumentTypeError
from collections import Counter
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bublik.core.argparse import parser_type_date
from bublik.core.importruns.reimport import RUN_CRITERIA, GradualReimport, imported_after
from bublik.core.run.external_links import get_sources
from bublik.data.models import TestIterationResult


HOURS_IN_DAY = 24


def parser_type_datetime(s):
    moment = parse_datetime(s)
    if moment is None:
        msg = f'Invalid date and time format: {s}'
        raise ArgumentTypeError(msg)
    # Without an offset, in UTC (TIME_ZONE)
    return moment if moment.tzinfo else timezone.make_aware(moment)


def parser_type_hours(s):
    try:
        start, end = (int(hour) for hour in s.split('-'))
    except ValueError:
        msg = f'Invalid hours format: {s}'
        raise ArgumentTypeError(msg) from None
    if not (0 <= start < HOURS_IN_DAY and 0 <= end < HOURS_IN_DAY) or start == end:
        msg = f'Invalid hours: {s}'
        raise ArgumentTypeError(msg)
    return start, end


class Command(BaseCommand):
    help = 'Import runs again with force, gradually, so that other imports are not held up'

    def add_arguments(self, parser):
        parser.add_argument(
            '-i',
            '--id',
            type=int,
            action='append',
            default=[],
            metavar='run_id',
            help='Run ID to import again (repeatable: -i 1 -i 2).',
        )
        parser.add_argument(
            '-f',
            '--from',
            type=parser_type_date,
            help='Select runs with start date >= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            help='Select runs with finish date <= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '-c',
            '--criterion',
            action='append',
            default=[],
            choices=sorted(RUN_CRITERIA),
            help=(
                'Select only runs meeting this criterion (repeatable). '
                + ' '.join(
                    f'{name}: {criterion.help}'
                    for name, criterion in sorted(RUN_CRITERIA.items())
                )
            ),
        )
        parser.add_argument(
            '--done-after',
            type=parser_type_datetime,
            metavar='datetime',
            help=(
                'Skip runs imported successfully since this moment (YYYY-MM-DD HH:MM '
                'in UTC, or with an offset, e.g. +03:00), '
                'e.g. since the previous start of the command.'
            ),
        )
        parser.add_argument(
            '--max-running',
            type=int,
            default=2,
            metavar='n',
            help=(
                'The most imports of the runs running at a time (default: 2). '
                'It is lowered to leave other imports at least one Celery worker process.'
            ),
        )
        parser.add_argument(
            '--hours',
            type=parser_type_hours,
            metavar='start-end',
            help=(
                'Schedule imports only in these hours of the day in UTC, '
                'the end excluded (e.g. 19-4).'
            ),
        )
        parser.add_argument(
            '--poll',
            type=float,
            default=30,
            metavar='seconds',
            help='Seconds between checks of the imports and the queue (default: 30).',
        )
        parser.add_argument(
            '--task-timeout',
            type=float,
            default=3,
            metavar='hours',
            help='Hours an import may run before it is revoked (default: 3).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help=(
                'Only report the runs that would be imported again, with counts of how '
                'they are selected, of those with no logs source found and per project.'
            ),
        )

    def check_options(self, options):
        if not options['id'] and not options['from'] and not options['to']:
            msg = 'Specify at least one of: -i, -f, -t.'
            raise CommandError(msg)
        if options['max_running'] < 1:
            msg = '--max-running must be at least 1.'
            raise CommandError(msg)
        if options['poll'] <= 0:
            msg = '--poll must be positive.'
            raise CommandError(msg)
        if options['task_timeout'] <= 0:
            msg = '--task-timeout must be positive.'
            raise CommandError(msg)

    def handle(self, *args, **options):
        self.check_options(options)
        run_ids = options['id']
        run_from = options['from']
        run_to = options['to']

        runs_query = Q(test_run=None, finish__isnull=False)
        if run_ids:
            runs_query &= Q(id__in=run_ids)
        if run_from:
            runs_query &= Q(start__date__gte=run_from)
        if run_to:
            runs_query &= Q(finish__date__lte=run_to)

        runs = TestIterationResult.objects.filter(runs_query)
        total = runs.count()
        stats = {'Finished runs selected by IDs and dates': total}
        for name in options['criterion']:
            runs = RUN_CRITERIA[name].filter(runs)

        # The criteria may be costly, so the runs meeting them are got once
        selected = list(runs.order_by('id').values_list('id', flat=True))
        if options['criterion']:
            stats['Not meeting the criteria'] = total - len(selected)

        done = set()
        if options['done_after']:
            done = set(
                imported_after(
                    TestIterationResult.objects.filter(id__in=selected),
                    options['done_after'],
                ).values_list('id', flat=True),
            )
            selected = [run_id for run_id in selected if run_id not in done]
            stats['Imported since --done-after'] = len(done)

        if run_ids:
            missing = set(run_ids) - set(selected) - done
            if missing:
                self.stdout.write(
                    self.style.WARNING(
                        f'Runs not found, unfinished or not meeting the criteria: '
                        f'{sorted(missing)}',
                    ),
                )
        if done:
            self.stdout.write(f'Skipped as imported since {options["done_after"]}: {len(done)}')

        self.stdout.write(f'Runs to import again: {len(selected)}')

        if options['dry_run']:
            self.write_dry_run_summary(selected, stats)
            return
        if not selected:
            return

        reimport = GradualReimport(
            selected,
            max_running=options['max_running'],
            hours=options['hours'],
            poll_interval=options['poll'],
            task_timeout=timedelta(hours=options['task_timeout']),
            notify=self.notify,
        )
        try:
            report = reimport.run()
        except KeyboardInterrupt:
            report = reimport.report
            self.stdout.write(self.style.WARNING('\nInterrupted.'))
        except Exception:
            # The imports scheduled go on, so what is known of them is reported
            self.write_summary(reimport.report)
            raise

        self.write_summary(report)

    def notify(self, message, outcome=None):
        styles = {
            'success': self.style.SUCCESS,
            'error': self.style.ERROR,
            'warning': self.style.WARNING,
        }
        self.stdout.write(styles[outcome](message) if outcome else message)

    def write_dry_run_summary(self, selected, stats):
        no_source = [run_id for run_id in selected if not get_sources(run_id)]
        projects = Counter(
            TestIterationResult.objects.filter(id__in=selected).values_list(
                'project__name',
                flat=True,
            ),
        )

        self.stdout.write('=========================================================')
        self.stdout.write('Import again summary (dry run, nothing is scheduled):')
        self.stdout.write('\nRuns to import again:')
        self.stdout.write(f'\t{selected}')
        if no_source:
            self.stdout.write('\nNo logs source found (would be skipped):')
            self.stdout.write(self.style.ERROR(f'\t{no_source}'))

        rows = [
            *((label, count, None) for label, count in stats.items()),
            ('To import again', len(selected), 'SUCCESS'),
            ('  of them with no logs source found', len(no_source), 'ERROR'),
            *(
                (f'  of them in project {name or "(none)"}', count, None)
                for name, count in sorted(projects.items(), key=lambda p: str(p[0]))
            ),
        ]

        self.stdout.write('=========================================================')
        self.stdout.write('Counts:')
        col_w = max(len(label) for label, _, _ in rows)
        for label, count, style in rows:
            line = f'\t{label:<{col_w}}  {count}'
            self.stdout.write(getattr(self.style, style)(line) if style else line)
        self.stdout.write('=========================================================')

    def write_summary(self, report):
        rows = [
            ('Imported', report.succeeded, 'SUCCESS'),
            ('Failed', report.failed, 'ERROR'),
            ('Not finished in time (revoked)', report.timed_out, 'ERROR'),
            ('Revoked otherwise', report.revoked, 'ERROR'),
            ('No logs source found', report.no_source, 'ERROR'),
            ('Still running (not waited for)', report.running, 'WARNING'),
            ('Left (not scheduled)', report.left, 'WARNING'),
        ]

        self.stdout.write('=========================================================')
        self.stdout.write('Import again summary:')
        for label, run_ids, style in rows[1:]:
            if run_ids:
                self.stdout.write(f'\n{label}:')
                self.stdout.write(getattr(self.style, style)(f'\t{run_ids}'))

        self.stdout.write('=========================================================')
        self.stdout.write('Counts:')
        col_w = max(len(label) for label, _, _ in rows)
        for label, run_ids, style in rows:
            self.stdout.write(getattr(self.style, style)(f'\t{label:<{col_w}}  {len(run_ids)}'))
        self.stdout.write('=========================================================')
