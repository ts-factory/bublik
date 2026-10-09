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
import. They have the same result and verdicts, but differ in tag expression,
keys or notes, and are shown as duplicates.

Expected results of a test iteration result are grouped by result and verdicts.
In a group, an expected result whose metas are all among the metas of another
one carries nothing of its own, and its link is removed. What is left of
a group is then a single expected result, unless some of them carry something
of their own (e.g. one a tag expression and the other a key). Such duplicates
are not touched, since removing any of them would lose data: the runs having
them are reported to be imported again, which replaces all their expected
results.

The removed links can be saved to a CSV file to be restored if needed.
"""

import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q

from bublik.core.argparse import parser_type_date
from bublik.data.models import Expectation, TestIterationResult


# Number of links removed in one transaction
DELETE_BATCH_SIZE = 10000

# For every group of expected results of a test iteration result with the same
# result and verdicts, the links to the expected results contained in another
# one of the group, and the number of the other links.
#
# An expected result is contained in another one if each of its expect metas
# (meta, serial and reference) is also an expect meta of the other one, and
# the other one has an expect meta of its own. The latter keeps both of two
# expected results with the same expect metas.
#
# Containment depends only on the expected results, and the same expected
# results are linked to many test iteration results, so it is checked once
# per pair of them.
DUPLICATES_QUERY = """
WITH run_links AS (
    SELECT er.id, er.expectation_id, er.testiterationresult_id, r.test_run_id
    FROM bublik_expectation_results er
    JOIN bublik_testiterationresult r ON r.id = er.testiterationresult_id
    WHERE r.test_run_id = ANY(%s)
),
expectation_signatures AS (
    SELECT em.expectation_id,
           string_agg(m.value, '') FILTER (WHERE m.type = 'result') AS status,
           array_agg(m.value ORDER BY em.serial)
               FILTER (WHERE m.type = 'verdict_expected') AS verdicts
    FROM bublik_expectmeta em
    JOIN bublik_meta m ON m.id = em.meta_id
    WHERE em.expectation_id IN (SELECT expectation_id FROM run_links)
    GROUP BY em.expectation_id
),
grouped_links AS (
    SELECT l.id, l.expectation_id, l.testiterationresult_id, l.test_run_id,
           dense_rank() OVER (
               PARTITION BY l.testiterationresult_id ORDER BY s.status, s.verdicts
           ) AS group_no,
           count(*) OVER (
               PARTITION BY l.testiterationresult_id, s.status, s.verdicts
           ) AS group_size
    FROM run_links l
    JOIN expectation_signatures s ON s.expectation_id = l.expectation_id
),
duplicate_links AS (
    SELECT id, expectation_id, testiterationresult_id, test_run_id, group_no
    FROM grouped_links
    WHERE group_size > 1
),
link_pairs AS (
    SELECT d.id AS inner_link_id,
           d.expectation_id AS inner_id,
           o.expectation_id AS outer_id
    FROM duplicate_links d
    JOIN duplicate_links o
      ON o.testiterationresult_id = d.testiterationresult_id
     AND o.group_no = d.group_no
     AND o.id <> d.id
),
contained_pairs AS (
    SELECT p.inner_id, p.outer_id
    FROM (SELECT DISTINCT inner_id, outer_id FROM link_pairs) p
    WHERE NOT EXISTS (
        SELECT 1
        FROM bublik_expectmeta i
        WHERE i.expectation_id = p.inner_id
          AND NOT EXISTS (
              SELECT 1
              FROM bublik_expectmeta o
              WHERE o.expectation_id = p.outer_id
                AND o.meta_id = i.meta_id
                AND o.serial = i.serial
                AND o.reference_id IS NOT DISTINCT FROM i.reference_id
          )
    )
    AND EXISTS (
        SELECT 1
        FROM bublik_expectmeta o
        WHERE o.expectation_id = p.outer_id
          AND NOT EXISTS (
              SELECT 1
              FROM bublik_expectmeta i
              WHERE i.expectation_id = p.inner_id
                AND i.meta_id = o.meta_id
                AND i.serial = o.serial
                AND i.reference_id IS NOT DISTINCT FROM o.reference_id
          )
    )
),
contained_links AS (
    SELECT DISTINCT p.inner_link_id AS id
    FROM link_pairs p
    JOIN contained_pairs c ON c.inner_id = p.inner_id AND c.outer_id = p.outer_id
),
classified_links AS (
    SELECT d.*, c.id IS NOT NULL AS contained
    FROM duplicate_links d
    LEFT JOIN contained_links c ON c.id = d.id
)
SELECT test_run_id,
       testiterationresult_id,
       coalesce(array_agg(id ORDER BY id) FILTER (WHERE contained), '{}') AS link_ids,
       coalesce(
           array_agg(expectation_id ORDER BY id) FILTER (WHERE contained), '{}'
       ) AS expectation_ids,
       count(*) FILTER (WHERE NOT contained) AS kept
FROM classified_links
GROUP BY testiterationresult_id, test_run_id, group_no
"""


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
                'Save the removed links to this CSV file '
                '(testiterationresult_id, expectation_id).'
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

        no_duplicates = sorted(set(found_ids) - fixed_runs - reimport_runs)
        # A run with duplicates left is reported only as one to be imported
        # again, even if some of its duplicates are removed
        fixed = sorted(fixed_runs - reimport_runs)
        reimport = sorted(reimport_runs)

        # --- Summary ---

        fixed_label = 'To be fixed (dry run)' if dry_run else 'Fixed'
        removed_label = 'Duplicates to remove (dry run)' if dry_run else 'Removed duplicates'

        self.stdout.write('=========================================================')
        self.stdout.write('Duplicated expected results fixing summary:')

        self.stdout.write('\nNo duplicates found:')
        self.stdout.write(f'\t{no_duplicates}')

        self.stdout.write(f'\n{fixed_label}:')
        self.stdout.write(self.style.SUCCESS(f'\t{fixed}'))

        self.stdout.write('\nImport again (duplicates carry data of their own):')
        self.stdout.write(self.style.ERROR(f'\t{reimport}'))

        self.stdout.write('=========================================================')

        self.stdout.write('Counts:')
        rows = [
            ('No duplicates found', len(no_duplicates), None),
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
        Return the links to the expected results contained in other ones
        as (link ID, test iteration result ID, expected result ID), IDs of runs
        having them, and IDs of runs with duplicates that carry data of their own.
        """
        stale_links = []
        fixed_runs = set()
        reimport_runs = set()

        # A server-side cursor, so that millions of groups are not loaded at once
        with transaction.atomic(), connection.chunked_cursor() as cursor:
            cursor.execute(DUPLICATES_QUERY, [run_ids])
            for run_id, result_id, link_ids, exp_ids, kept in cursor:
                if link_ids:
                    stale_links.extend(
                        (link_id, result_id, exp_id)
                        for link_id, exp_id in zip(link_ids, exp_ids, strict=True)
                    )
                    fixed_runs.add(run_id)
                if kept > 1:
                    reimport_runs.add(run_id)

        return stale_links, fixed_runs, reimport_runs
