# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

"""
Management command: reimport_duplicated_expectations
Usage: python manage.py reimport_duplicated_expectations [-i <id> ...] [-f <date>] [-t <date>]
                                                         [--dry-run]

Finds runs with duplicated expected results and schedules their forced
re-import, which replaces the expected results of each test iteration result
with the ones from the run log.

A test iteration result is considered to have duplicated expected results if
several of its expectations have the same result and the same verdicts.
"""

from collections import defaultdict
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q

from bublik.core.argparse import parser_type_date
from bublik.core.run.external_links import get_sources
from bublik.core.utils import create_import_job
from bublik.data.models import Expectation, ExpectMeta, TestIterationResult
from bublik.interfaces.celery import tasks


# Width of the label column in run status output
LABEL_W = 12

# Run status output indentation prefix
L2 = '\t'


class Command(BaseCommand):
    help = 'Re-import runs with duplicated expected results'

    def add_arguments(self, parser):
        parser.add_argument(
            '-i',
            '--id',
            type=int,
            action='append',
            default=[],
            metavar='run_id',
            help='Run ID to check (repeatable: -i 1 -i 2).',
        )
        parser.add_argument(
            '-f',
            '--from',
            type=parser_type_date,
            help='Check runs with start date >= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            help='Check runs with finish date <= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only list affected runs without scheduling their re-import.',
        )

    def handle(self, *args, **options):
        run_ids = options['id']
        run_from = options['from']
        run_to = options['to']

        if not run_ids and not run_from and not run_to:
            msg = 'Specify at least one of: -i, -f, -t.'
            raise CommandError(msg)

        runs_query = Q(test_run=None)
        if run_ids:
            runs_query &= Q(id__in=run_ids)
        if run_from:
            runs_query &= Q(start__date__gte=run_from)
        if run_to:
            runs_query &= Q(finish__date__lte=run_to)

        run_qs = TestIterationResult.objects.filter(runs_query).order_by('id')

        if not run_qs.exists():
            msg = 'No runs found matching the given parameters.'
            raise CommandError(msg)

        found_ids = list(run_qs.values_list('id', flat=True))
        total = len(found_ids)

        if run_ids:
            missing = set(run_ids) - set(found_ids)
            if missing:
                self.stdout.write(self.style.WARNING(f'Runs not found: {sorted(missing)}'))

        self.stdout.write(
            f'Checking {total} run(s) for duplicated expected results: {found_ids}',
        )

        dry_run = options['dry_run']
        affected_run_ids = self.find_affected_runs(run_qs)
        not_affected = sorted(set(found_ids) - set(affected_run_ids))
        scheduled = []
        no_source = []

        requesting_host = (
            f'http://{settings.BUBLIK_HOST}/{settings.URL_PREFIX}'.rstrip('/') + '/'
        )

        affected_runs = TestIterationResult.objects.filter(id__in=affected_run_ids)
        for run in affected_runs.select_related('project').order_by('id'):
            self.stdout.write(f'Processing run {run.id}')

            run_source_url = get_sources(run)
            if not run_source_url:
                no_source.append(run.id)
                self._write('Status', 'skipped (no source URL)', self.style.ERROR)
                continue

            self._write('Source URL', run_source_url)

            if dry_run:
                self._write('Status', 're-import needed (dry run)', self.style.WARNING)
                scheduled.append(run.id)
                continue

            import_job = create_import_job(run_source_url)
            try:
                task_id = tasks.importruns.delay(
                    requesting_host,
                    import_job.id,
                    run_source_url,
                    project_name=run.project.name if run.project else None,
                    date_from=None,
                    date_to=None,
                    force=True,
                )
            finally:
                import_job.finished_at = datetime.now()
                import_job.save()

            scheduled.append(run.id)
            self._write('Task ID', str(task_id))
            self._write('Status', 're-import scheduled', self.style.SUCCESS)

        # --- Summary ---

        scheduled_label = 'Re-import needed (dry run)' if dry_run else 'Re-import scheduled'

        self.stdout.write('=========================================================')
        self.stdout.write('Duplicated expected results fixing summary:')

        self.stdout.write('\nNo duplicates found:')
        self.stdout.write(f'\t{not_affected}')

        self.stdout.write(f'\n{scheduled_label}:')
        self.stdout.write(self.style.SUCCESS(f'\t{scheduled}'))

        self.stdout.write('\nManual review required:')
        self.stdout.write(self.style.ERROR(f'\tNo source URL: {no_source}'))

        self.stdout.write('=========================================================')

        self.stdout.write('Counts:')
        rows = [
            ('No duplicates found', len(not_affected), None),
            (scheduled_label, len(scheduled), 'SUCCESS'),
            ('No source URL', len(no_source), 'ERROR'),
        ]
        col_w = max(len(label) for label, _, _ in rows)
        for label, count, style in rows:
            line = f'\t{label:<{col_w}}  {count}'
            if style:
                line = getattr(self.style, style)(line)
            self.stdout.write(line)

        self.stdout.write('=========================================================')

    def _write(self, label, value, style=None):
        line = f'{L2}{label + ":":<{LABEL_W}} {value}'
        self.stdout.write(style(line) if style else line)

    def find_affected_runs(self, runs):
        # Only results with several expectations can have duplicated ones
        result_ids = (
            TestIterationResult.objects.filter(test_run__in=runs)
            .annotate(expectations_count=Count('expectations'))
            .filter(expectations_count__gt=1)
            .values_list('id', flat=True)
        )

        result_expectations = defaultdict(list)
        result_runs = {}
        links = Expectation.results.through.objects.filter(
            testiterationresult_id__in=result_ids,
        ).values_list(
            'testiterationresult_id', 'testiterationresult__test_run_id', 'expectation_id'
        )
        for result_id, run_id, expectation_id in links:
            result_expectations[result_id].append(expectation_id)
            result_runs[result_id] = run_id

        # Expectations are shared between results, so collect their results
        # and verdicts once
        expectation_ids = set().union(*result_expectations.values())
        expectation_results = {}
        expectation_verdicts = defaultdict(list)
        expect_metas = (
            ExpectMeta.objects.filter(
                expectation_id__in=expectation_ids,
                meta__type__in=['result', 'verdict_expected'],
            )
            .order_by('serial', 'id')
            .values_list('expectation_id', 'meta__type', 'meta__value')
        )
        for expectation_id, meta_type, value in expect_metas:
            if meta_type == 'result':
                expectation_results[expectation_id] = value
            else:
                expectation_verdicts[expectation_id].append(value)

        def has_duplicates(expectation_ids):
            signatures = [
                (expectation_results.get(e_id), tuple(expectation_verdicts[e_id]))
                for e_id in expectation_ids
            ]
            return len(signatures) != len(set(signatures))

        return sorted(
            {
                result_runs[result_id]
                for result_id, expectation_ids in result_expectations.items()
                if has_duplicates(expectation_ids)
            },
        )
