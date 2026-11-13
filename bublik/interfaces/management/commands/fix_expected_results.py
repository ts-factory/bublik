# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

"""
Management command: fix_expected_results
Usage: python manage.py fix_expected_results [-i <id> ...] [-f <date>] [-t <date>]
       [-o <file>] [--dry-run]

Removes duplicated expected results of runs.

Force imports used to add the expected results of a test iteration result
without removing the existing ones, so runs imported again after the way
expected results are stored had changed kept the expected results of every
import (e.g. of a live import and of the import of the run log following it).
They have the same result and verdicts, but differ in tag expression, keys or
notes, and are shown as duplicates.

Expected results of a test iteration result are grouped by result and verdicts.
In a group, an expected result whose metas are all among the metas of another
one from another import carries nothing of its own, and its link is removed.
Expected results of one import are never removed: one of them may legitimately
be contained in another one.

Two expected results of a test iteration result come from different imports if
they have different tag expressions, as an import gives all expected results of
a test iteration result the same one, or if links of other test iteration
results of the run were added between their links, as an import adds the links
of a test iteration result one after another. Expected results of different
imports with the same tag expression are thus taken for ones of one import and
left untouched if no links of other test iteration results lie between theirs
(e.g. in runs of a single test iteration result).

What is left of a group is then a single expected result or expected results of
one import, unless expected results of different imports carry something of
their own (e.g. one a tag expression and the other a key). Such duplicates are
not touched, since removing any of them would lose data: the runs having them
are reported to be imported again, which replaces all their expected results.

The removed links can be saved to a CSV file to be restored if needed.
"""

import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from bublik.core.argparse import parser_type_date
from bublik.core.run.data import find_duplicates
from bublik.data.models import Expectation, TestIterationResult


# Number of links removed in one transaction
DELETE_BATCH_SIZE = 10000


class Command(BaseCommand):
    help = 'Remove duplicated expected results of runs'

    def add_arguments(self, parser):
        parser.add_argument(
            '-i',
            '--id',
            type=int,
            action='append',
            default=[],
            metavar='run_id',
            help='Run ID to fix (repeatable: -i 1 -i 2).',
        )
        parser.add_argument(
            '-f',
            '--from',
            type=parser_type_date,
            help='Fix runs with start date >= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            help='Fix runs with finish date <= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '-o',
            '--output',
            metavar='file',
            help=(
                'Save the removed links (with --dry-run, the links to be removed) '
                'to this CSV file (testiterationresult_id, expectation_id).'
            ),
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report what would be removed.',
        )

    def handle(self, *args, **options):
        run_ids = options['id']
        run_from = options['from']
        run_to = options['to']
        output = options['output']
        dry_run = options['dry_run']

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

        found_ids = list(
            TestIterationResult.objects.filter(runs_query)
            .order_by('id')
            .values_list('id', flat=True),
        )

        if not found_ids:
            msg = 'No runs found matching the given parameters.'
            raise CommandError(msg)

        if run_ids:
            missing = set(run_ids) - set(found_ids)
            if missing:
                self.stdout.write(self.style.WARNING(f'Runs not found: {sorted(missing)}'))

        self.stdout.write(
            f'Checking {len(found_ids)} run(s) for duplicated expected results: {found_ids}',
        )

        stale_links, fixed_runs, reimport_runs = self.find_stale_links(found_ids)

        # The links are saved before they are removed, so that the file covers
        # the removed ones even if removing fails halfway
        if output:
            with open(output, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['testiterationresult_id', 'expectation_id'])
                writer.writerows((result_id, exp_id) for _, result_id, exp_id in stale_links)

        if not dry_run:
            link_model = Expectation.results.through
            link_ids = [link_id for link_id, _, _ in stale_links]
            for start in range(0, len(link_ids), DELETE_BATCH_SIZE):
                with transaction.atomic():
                    link_model.objects.filter(
                        id__in=link_ids[start : start + DELETE_BATCH_SIZE],
                    ).delete()

        nothing_to_fix = sorted(set(found_ids) - fixed_runs - reimport_runs)
        # A run with duplicates left is reported only as one to be imported
        # again, even if some of its duplicates are removed
        fixed = sorted(fixed_runs - reimport_runs)
        reimport = sorted(reimport_runs)

        # --- Summary ---

        fixed_label = 'To be fixed (dry run)' if dry_run else 'Fixed'
        removed_label = 'Duplicates to remove (dry run)' if dry_run else 'Removed duplicates'

        self.stdout.write('=========================================================')
        self.stdout.write('Duplicated expected results fixing summary:')

        self.stdout.write('\nNothing to fix:')
        self.stdout.write(f'\t{nothing_to_fix}')

        self.stdout.write(f'\n{fixed_label}:')
        self.stdout.write(self.style.SUCCESS(f'\t{fixed}'))

        self.stdout.write('\nImport again (duplicates carry data of their own):')
        self.stdout.write(self.style.ERROR(f'\t{reimport}'))

        self.stdout.write('=========================================================')

        self.stdout.write('Counts:')
        rows = [
            ('Nothing to fix', len(nothing_to_fix), None),
            (fixed_label, len(fixed), 'SUCCESS'),
            (removed_label, len(stale_links), 'SUCCESS'),
            ('Import again', len(reimport), 'ERROR'),
        ]
        col_w = max(len(label) for label, _, _ in rows)
        for label, count, style in rows:
            line = f'\t{label:<{col_w}}  {count}'
            if style:
                line = getattr(self.style, style)(line)
            self.stdout.write(line)

        self.stdout.write('=========================================================')

    def find_stale_links(self, run_ids):
        """
        Return the links to the expected results contained in other ones from
        other imports as (link ID, test iteration result ID, expected result ID),
        IDs of runs having them, and IDs of runs with duplicates of different
        imports that carry data of their own.
        """
        stale_links = []
        fixed_runs = set()
        reimport_runs = set()

        for run_id, result_id, link_ids, exp_ids, mixed in find_duplicates(run_ids):
            if link_ids:
                stale_links.extend(
                    (link_id, result_id, exp_id)
                    for link_id, exp_id in zip(link_ids, exp_ids, strict=True)
                )
                fixed_runs.add(run_id)
            if mixed:
                reimport_runs.add(run_id)

        return stale_links, fixed_runs, reimport_runs
