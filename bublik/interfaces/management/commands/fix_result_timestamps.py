"""
Management command: fix_result_timestamps
Usage: python manage.py fix_result_timestamps [-i <id> ...] [-f <date>] [-t <date>]

Repairs corrupted start/finish timestamps in TestIterationResult trees.

Structure assumed:
  - A *run* is a TestIterationResult with test_run_id=None. It carries only
    start, finish, and project. Its timestamps are never modified: run.start
    is used to compute the timezone delta and as the sanity-check lower bound;
    run.finish is used only in the sanity check.
  - *Packages* are direct children of the run: TestIterationResult objects
    with test_run_id=run.pk and parent_package_id=None. Each package is the
    root of an independent subtree.
  - All deeper nodes have both test_run_id=run.pk and a non-null
    parent_package_id, forming an arbitrarily deep tree beneath each package.

Two correction stages are applied per run:

  Stage 1 — Midnight-crossing fix (DFS tree traversal):
    The full tree is traversed with run as the virtual root. Packages are
    treated as its direct children (siblings at the top level), so the
    prev -> next sibling logic is applied between packages just as it is at
    every deeper level. Run boundaries are ground truth and must never be modified.

    Fixes applied at every level:

    1. finish < start on a leaf node            -> finish += 1 day
       finish - start >= 24h on a leaf node     -> finish -= 1 day
       finish < start on a non-leaf node        -> finish += 1 day
       (the >= 24h subtraction is NOT applied to non-leaf nodes, because
        a non-leaf finish spans its entire subtree and may legitimately
        be large)
    2. next.start < prev.finish for siblings    -> next.start += 1 day
       next.start - prev.finish >= 24h          -> next.start -= 1 day
       (siblings are ordered by (exec_seqno, pk), applied at every level)
    3. child.start < parent.start               -> child.start += 1 day
       child.start - parent.start >= 24h        -> child.start -= 1 day
       (skipped for the first package: its start is not aligned against run.start)
    4. parent.finish < last_child.finish        -> parent.finish += 1 day
       parent.finish - last_child.finish >= 24h -> parent.finish -= 1 day
       (skipped for run — its finish is never modified)

    Note: package starts are NOT adjusted relative to run.start in Stage 1,
    and the last package's finish is NOT adjusted relative to run.finish.
    These boundary relationships are enforced only by Stage 2 (timezone fix).

    Each fix adjusts a timestamp by exactly +-1 day. If a single adjustment is not
    enough to satisfy the condition, a ValueError is raised and all changes for the
    run are rolled back. This is intentional: the command is designed to correct
    clock-skew and midnight-crossing errors only, not arbitrary corruption that would
    require multi-day shifts.

  Stage 2 — Timezone normalization:
    All result timestamps are shifted by a whole-hour delta so that the first
    package starts at or after run.start and the last package finishes at or
    before run.finish. Two per-side deltas are computed:
      delta_start = largest whole-hour H such that first_pkg.start - H >= run.start
      delta_finish = smallest whole-hour H such that last_pkg.finish - H <= run.finish
    The delta with the smallest absolute magnitude is applied to all result
    timestamps. If both deltas are zero, no update is performed. This corrects
    a systematic offset where timestamps were recorded in local time but stored
    as UTC.

Processing pipeline per run:

  0. Unable to process checks:
       - No data (no results or no packages)        -> stop, record as Skip/no_data
       - Null timestamps (any timestamp is None)    -> stop, record as Skip/null_timestamps

  1. Stage 1 — Midnight-crossing fix:
       - ValueError raised during tree traversal -> record as Midnight/failed_fix_error, stop.
       - Stage 1 completes (with or without changes) -> continue to Stage 2.

  2. Stage 2 — Timezone fix:
       - Delta is zero: no update performed     -> will be recorded as Timezone/not_needed.
       - Delta is non-zero: timestamps shifted  -> will be recorded as Timezone/fixed.
       - Unexpected exception                   -> record as Timezone/failed_fix_error.

  3. Sanity check:
       - Applied after Stage 2.
       - Failure -> both stages marked as failed_sanity, all changes rolled back.

  Transaction model:
    Stage 1 and Stage 2 each run inside their own transaction.atomic() block.
    Both are wrapped in an outer transaction.atomic() that is rolled back on a
    SanityError from the final sanity check, reverting changes from both stages.
    A failure in Stage 1 (fix error) stops the pipeline; its inner savepoint is
    automatically rolled back, so no database writes from Stage 1 persist. A
    failure in Stage 2 (fix error) leaves Stage 1 changes intact unless
    the final sanity check triggers a full rollback.

Sanity check:
  After Stage 2 (_check_run_boundaries):
    - first_pkg.start >= run.start
    - last_pkg.finish <= run.finish  (delta_finish <= 0)
    - the gap between last_pkg.finish and run.finish is < ONE_DAY
    - first_pkg.start <= last_pkg.finish

Logging:
  A unified _log() method routes output based on context:
    - Inside a Celery task (TASK_ID env var is set): writes to the task logger.
      Labels are written as-is (expected to start with a lowercase letter).
      Progress/status lines that are only meaningful in a terminal are
      suppressed (terminal_only=True).
    - In a terminal (manage.py): writes to stdout with indentation and colour.
      The first letter of each label is capitalised automatically.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import math
import os
from typing import Literal, NamedTuple

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q

from bublik.core.argparse import parser_type_date
from bublik.core.exceptions import SanityError
from bublik.core.logging import get_task_or_server_logger
from bublik.data.models import TestIterationResult


ONE_DAY = timedelta(days=1)

# Width of the label column in terminal boundary output (stdout only)
LABEL_W = 34

# Terminal-output indentation prefix (stdout only)
L2 = '\t'


# ---------------------------------------------------------------------------
# Result categories
# ---------------------------------------------------------------------------

SkippedReason = Literal[
    'no_data',
    'null_timestamps',
]
FixStatus = Literal[
    'not_needed',
    'fixed',
    'failed_sanity',
    'failed_fix_error',
]


class RunResult(NamedTuple):
    """
    Categorisation of a single run after the full pipeline.

    Attributes:
        skipped_reason: Set when the run could not be processed (no_data,
            null_timestamps). When set, both midnight_fix_status and
            timezone_fix_status are None.
        midnight_fix_status: Result of the midnight-crossing fix. None when
            skipped_reason is set.
        timezone_fix_status: Result of the timezone fix. None when
            skipped_reason is set, or when Stage 1 failed before Stage 2
            was reached.
        error_msg: Human-readable failure description, or None if the run
            succeeded or was skipped (no_data, null_timestamps).
    """

    skipped_reason: SkippedReason | None
    midnight_fix_status: FixStatus | None
    timezone_fix_status: FixStatus | None
    error_msg: str | None


# ---------------------------------------------------------------------------
# Timezone normalization helpers
# ---------------------------------------------------------------------------


def _get_delta_start(run_start: datetime, first_pkg_start: datetime) -> timedelta:
    """
    Return the largest whole-hour timedelta H such that
    (first_pkg_start - H) >= run_start.
    """
    diff_seconds = (first_pkg_start - run_start).total_seconds()
    return timedelta(hours=math.floor(diff_seconds / 3600))


def _get_delta_finish(run_finish: datetime, last_pkg_finish: datetime) -> timedelta:
    """
    Return the smallest whole-hour timedelta H such that
    (last_pkg_finish - H) <= run_finish.
    """
    diff_seconds = (last_pkg_finish - run_finish).total_seconds()
    return timedelta(hours=math.ceil(diff_seconds / 3600))


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------


def _children_map(
    run_results: list[TestIterationResult],
) -> dict[int, list[TestIterationResult]]:
    """
    Build a parent_pk -> [children] map for the full run tree, with run as
    the virtual root.

    Packages (parent_package_id=None) are placed under run_id so that the
    DFS traversal treats them as siblings at the top level, applying the same
    prev -> next sibling logic as at every deeper level.

    Children lists are sorted by (exec_seqno, pk) to guarantee a stable,
    deterministic traversal order independent of dict insertion order.
    """
    children: dict[int, list[TestIterationResult]] = defaultdict(list)

    for result in run_results:
        key = (
            result.parent_package_id
            if result.parent_package_id is not None
            else result.test_run_id
        )
        children[key].append(result)

    for lst in children.values():
        lst.sort(key=lambda r: (r.exec_seqno, r.pk))

    return children


def _collect_run_data(run, run_results):
    run_results_list = list(run_results)
    ch_map = _children_map(run_results_list)
    packages = ch_map.get(run.pk, [])
    return run_results_list, ch_map, packages


# ---------------------------------------------------------------------------
# Fix logic
# ---------------------------------------------------------------------------


def _fix_run_tree(
    run,
    children_map: dict[int, list[TestIterationResult]],
    changed: set[int],
) -> None:
    """
    DFS traversal that fixes midnight-crossing errors in the full run tree.
    run is the virtual root used only for traversal entry and parent_start
    anchoring; its finish is never modified.

    Each fix adds or subtracts exactly 1 day as needed:

      DOWN   -- child.start < parent.start              -> child.start   += 1 day
             -- child.start - parent.start >= 24h       -> child.start   -= 1 day
             (skipped for the first package: its start is not anchored against
              run.start in Stage 1; that relationship is enforced by Stage 2 only)
      LEVEL  -- cur.finish  < cur.start                 -> cur.finish    += 1 day
             -- cur.finish - cur.start >= 24h (leaf)    -> cur.finish    -= 1 day
             -- next.start  < prev.finish               -> next.start    += 1 day
             -- next.start - prev.finish >= 24h         -> next.start    -= 1 day
      UP     -- parent.finish < last_child.finish       -> parent.finish += 1 day
             -- parent.finish - last_child.finish >= 24h -> parent.finish -= 1 day
             (skipped for run -- its finish is never modified)

    If after a +-1-day fix the condition still fails, raises ValueError so the
    caller can roll back the entire run. This means the command cannot repair
    corruption that requires a multi-day shift; such runs must be inspected
    manually.

    Note on traversal order: _fix_self_finish for a node is intentionally
    deferred until after _visit() returns for that node. This ensures that
    the node's finish has already been anchored by its last child (UP step)
    before we compare it against the node's own start (LEVEL step). For the
    first child specifically, _fix_self_finish is called at the start of the
    second iteration (when it becomes `prev`), not immediately after
    _visit(first) — the effect is the same because no sibling comparison
    happens before that point.
    """

    def _fix_start(node: TestIterationResult, prev_timestamp: datetime) -> None:
        """Align node.start with prev_timestamp by +-1 day if the gap is wrong."""
        if node.start < prev_timestamp:
            node.start += ONE_DAY
            changed.add(node.pk)
            if node.start < prev_timestamp:
                msg = (
                    f'TIR {node.pk}: start still < previous timestamp '
                    f'after +1 day ({node.start} < {prev_timestamp})'
                )
                raise ValueError(msg)
        elif node.start - prev_timestamp >= ONE_DAY:
            node.start -= ONE_DAY
            changed.add(node.pk)
            if node.start - prev_timestamp >= ONE_DAY:
                msg = (
                    f'TIR {node.pk}: start still >= previous timestamp + 24h '
                    f'after -1 day ({node.start} - {prev_timestamp} >= 24h)'
                )
                raise ValueError(msg)

    def _fix_finish(node: TestIterationResult, prev_timestamp: datetime) -> None:
        """Align node.finish with prev_timestamp by +-1 day if the gap is wrong."""
        if node.finish is None:
            return
        if node.finish < prev_timestamp:
            node.finish += ONE_DAY
            changed.add(node.pk)
            if node.finish < prev_timestamp:
                msg = (
                    f'TIR {node.pk}: finish still < previous timestamp '
                    f'after +1 day ({node.finish} < {prev_timestamp})'
                )
                raise ValueError(msg)
        elif node.finish - prev_timestamp >= ONE_DAY:
            node.finish -= ONE_DAY
            changed.add(node.pk)
            if node.finish - prev_timestamp >= ONE_DAY:
                msg = (
                    f'TIR {node.pk}: finish still >= previous timestamp + 24h '
                    f'after -1 day ({node.finish} - {prev_timestamp} >= 24h)'
                )
                raise ValueError(msg)

    def _fix_self_finish(node: TestIterationResult, is_leaf: bool) -> None:
        """
        Align node.finish with node.start by +-1 day if the gap is wrong.

        For leaf nodes: both finish < start and finish - start >= 24h are fixed.
        For non-leaf nodes: only finish < start is fixed.
        """
        if node.finish is None:
            return
        if node.finish < node.start:
            node.finish += ONE_DAY
            changed.add(node.pk)
            if node.finish < node.start:
                msg = (
                    f'TIR {node.pk}: finish still < start '
                    f'after +1 day ({node.finish} < {node.start})'
                )
                raise ValueError(msg)
        elif is_leaf and node.finish - node.start >= ONE_DAY:
            node.finish -= ONE_DAY
            changed.add(node.pk)
            if node.finish - node.start >= ONE_DAY:
                msg = (
                    f'TIR {node.pk}: finish still >= start + 24h '
                    f'after -1 day ({node.finish} - {node.start} >= 24h)'
                )
                raise ValueError(msg)

    def _visit(node: TestIterationResult) -> None:
        children = children_map.get(node.pk, [])

        if not children:
            _fix_self_finish(node, is_leaf=True)
            return

        first = children[0]
        # Do NOT align the first package's start against run.start in Stage 1.
        # That boundary relationship is enforced exclusively by Stage 2 (timezone fix).
        if node.pk != run.pk:
            _fix_start(first, node.start)
        _visit(first)
        prev = first

        for nxt in children[1:]:
            prev_is_leaf = prev.pk not in children_map
            _fix_self_finish(prev, is_leaf=prev_is_leaf)
            if prev.finish is not None:
                _fix_start(nxt, prev.finish)
            _visit(nxt)
            prev = nxt

        last_is_leaf = prev.pk not in children_map
        _fix_self_finish(prev, is_leaf=last_is_leaf)

        # run boundaries are ground truth and must never be modified
        if prev.finish is not None and node.pk != run.pk:
            _fix_finish(node, prev.finish)

    _visit(run)


# ---------------------------------------------------------------------------
# Sanity check
# ---------------------------------------------------------------------------


def _check_run_boundaries(run, packages):
    first_pkg = packages[0]
    last_pkg = packages[-1]

    # If run.finish has no microseconds, it was likely stored with truncated
    # precision; treat it as the last possible moment of that second so that
    # a package finishing within the same second is not falsely rejected.
    run_finish = (
        run.finish if run.finish.microsecond != 0 else run.finish.replace(microsecond=999999)
    )

    delta_finish = _get_delta_finish(run_finish, last_pkg.finish)

    return (
        first_pkg.start >= run.start
        and first_pkg.start <= last_pkg.finish
        and delta_finish <= timedelta(0)
        and abs(delta_finish) < ONE_DAY
    )


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        'Repair corrupted start/finish timestamps in TestIterationResult trees. '
        'Select runs by -i (IDs), -f (from date), -t (to date), or any combination.'
    )

    def _log(
        self,
        level: str,
        label: str,
        value: str = '',
        *,
        style=None,
        indent: bool = True,
        terminal_only: bool = False,
        logger_only: bool = False,
    ) -> None:
        """
        Route output to the task logger or stdout depending on context.

        Writes to the task logger when TASK_ID env var is set (Celery task),
        or to stdout with terminal formatting otherwise.

        Args:
            level:         Logger level string ('info', 'warning', 'error', ...).
                           Ignored when writing to stdout.
            label:         Human-readable label. Expected to start with a
                           lowercase letter. Written as-is to the task logger;
                           the first letter is capitalised automatically for stdout.
            value:         Optional value appended after the label. In stdout
                           it is right-aligned to LABEL_W; in the logger it
                           follows a colon after the label.
            style:         Optional self.style.* callable applied to the whole
                           stdout line. Ignored in task-logger mode.
            indent:        Prefix the stdout line with L2 (default True).
                           Ignored in task-logger mode.
            terminal_only: If True, skip task-logger output entirely.
                           Use for progress/status lines that are only
                           meaningful in an interactive terminal session.
            logger_only:   If True, skip stdout output entirely.
                           Use for messages that are only meaningful in a task
                           context (e.g. final success confirmation already
                           implied by the terminal status line).
        """
        if os.getenv('TASK_ID') is not None:
            if not terminal_only:
                msg = f'{label}: {value}' if value else label
                getattr(get_task_or_server_logger(), level)(msg)
        elif not logger_only:
            prefix = L2 if indent else ''
            capitalized = label[0].upper() + label[1:] if label else label
            line = (
                f'{prefix}{capitalized:<{LABEL_W}} {value}'
                if value
                else f'{prefix}{capitalized}'
            )
            self.stdout.write(style(line) if style else line)

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
            help='Process runs with start date >= this date (YYYY.MM.DD).',
        )
        parser.add_argument(
            '-t',
            '--to',
            type=parser_type_date,
            help='Process runs with finish date <= this date (YYYY.MM.DD).',
        )

    def handle(self, *args, **options):
        # Get and validate options
        run_ids: list[int] = options['id']
        run_from = options['from']
        run_to = options['to']

        if not run_ids and not run_from and not run_to:
            msg = 'Specify at least one of: -i, -f, -t.'
            raise CommandError(msg)

        # Get and validate run IDs
        query = Q(test_run=None)
        if run_ids:
            query &= Q(id__in=run_ids)
        if run_from:
            query &= Q(start__date__gte=run_from)
        if run_to:
            query &= Q(finish__date__lte=run_to)

        run_qs = TestIterationResult.objects.filter(query).order_by('id')

        if not run_qs.exists():
            msg = 'No runs found matching the given parameters.'
            raise CommandError(msg)

        total = run_qs.count()
        found_ids = list(run_qs.values_list('id', flat=True))

        if run_ids:
            missing = set(run_ids) - set(found_ids)
            if missing:
                self.stdout.write(self.style.WARNING(f'Runs not found: {sorted(missing)}'))

        self.stdout.write(f'Fixing timestamps for {total} run(s): {found_ids}')

        # Accumulate run PKs per outcome bucket.
        # Keys are the literal string values of
        # SkippedReason / FixStatus.
        skipped_reason: dict[SkippedReason, list[int]] = defaultdict(list)
        midnight_fix_status: dict[FixStatus, list[int]] = defaultdict(list)
        timezone_fix_status: dict[FixStatus, list[int]] = defaultdict(list)

        in_task = os.getenv('TASK_ID') is not None

        for run in run_qs.iterator(chunk_size=100):
            self._log('info', f'processing run {run.pk}', indent=False, terminal_only=True)

            result = self._fix_run(run)

            if in_task and result.error_msg:
                raise CommandError(result.error_msg)

            if result.skipped_reason:
                skipped_reason[result.skipped_reason].append(run.pk)
                self._log(
                    'info',
                    f'status: skipped ({result.skipped_reason})',
                    terminal_only=True,
                )
            else:
                if result.timezone_fix_status is not None:
                    timezone_fix_status[result.timezone_fix_status].append(run.pk)
                if result.midnight_fix_status is not None:
                    midnight_fix_status[result.midnight_fix_status].append(run.pk)

                mc = result.midnight_fix_status
                tz = result.timezone_fix_status

                is_failed = mc in ('failed_fix_error', 'failed_sanity') or (
                    mc != 'fixed' and tz in ('failed_fix_error', 'failed_sanity')
                )
                mc_fixed_only = mc == 'fixed' and tz in (
                    'not_needed',
                    'failed_fix_error',
                )
                tz_fixed_only = mc == 'not_needed' and tz == 'fixed'
                fully_fixed = mc == 'fixed' and tz == 'fixed'

                if is_failed:
                    self._log(
                        'warning',
                        f'status: failed ({result.error_msg})',
                        style=self.style.ERROR,
                        terminal_only=True,
                    )
                elif mc_fixed_only:
                    self._log(
                        'info',
                        'status: midnight only fixed',
                        style=(
                            self.style.WARNING
                            if tz == 'failed_fix_error'
                            else self.style.SUCCESS
                        ),
                        terminal_only=True,
                    )
                elif tz_fixed_only:
                    self._log(
                        'info',
                        'status: timezone only fixed',
                        style=self.style.SUCCESS,
                        terminal_only=True,
                    )
                elif fully_fixed:
                    self._log(
                        'info',
                        'status: midnight and timezone fixed',
                        style=self.style.SUCCESS,
                        terminal_only=True,
                    )
                else:
                    self._log('info', 'status: no changes needed', terminal_only=True)

        # --- Summary ---

        processed_num = sum(len(v) for v in skipped_reason.values()) + sum(
            len(v) for v in midnight_fix_status.values()
        )
        no_fixes_needed = sorted(
            set(midnight_fix_status['not_needed']) & set(timezone_fix_status['not_needed']),
        )
        fully_fixed = sorted(
            set(midnight_fix_status['fixed']) & set(timezone_fix_status['fixed']),
        )
        midnight_only = sorted(
            set(midnight_fix_status['fixed']) - set(timezone_fix_status['fixed']),
        )
        timezone_only = sorted(
            set(timezone_fix_status['fixed']) - set(midnight_fix_status['fixed']),
        )
        sanity_failed = sorted(
            set(midnight_fix_status['failed_sanity'])
            | set(timezone_fix_status['failed_sanity']),
        )
        mc_fix_failed = midnight_fix_status['failed_fix_error']
        tz_fix_failed = timezone_fix_status['failed_fix_error']

        self.stdout.write('=========================================================')
        self.stdout.write('Results timestamp fixing summary:')

        self.stdout.write(f'\nProcessed {processed_num}/{total} runs')

        self.stdout.write('\nSkipped:')
        self.stdout.write(f'\tNo data: {skipped_reason["no_data"]}')
        self.stdout.write(
            self.style.WARNING(
                f'\tNull timestamps (cannot validate): {skipped_reason["null_timestamps"]}',
            ),
        )

        self.stdout.write('\nNo fixes needed:')
        self.stdout.write(f'\t{no_fixes_needed}')

        self.stdout.write('\nFixed:')
        self.stdout.write(self.style.SUCCESS(f'\tTimezone only fixed: {timezone_only}'))
        self.stdout.write(self.style.SUCCESS(f'\tMidnight only fixed: {midnight_only}'))
        self.stdout.write(
            self.style.SUCCESS(f'\tMidnight and timezone fixed: {fully_fixed}'),
        )

        self.stdout.write('\nManual review required:')
        self.stdout.write(self.style.ERROR(f'\tTimezone fix error: {tz_fix_failed}'))
        self.stdout.write(self.style.ERROR(f'\tMidnight fix error: {mc_fix_failed}'))
        self.stdout.write(self.style.ERROR(f'\tSanity check failed: {sanity_failed}'))

        self.stdout.write('\n=========================================================')

        self.stdout.write('Counts:')
        rows = [
            ('No data', len(skipped_reason['no_data']), None),
            ('Null timestamps', len(skipped_reason['null_timestamps']), 'WARNING'),
            ('No fixes needed', len(no_fixes_needed), None),
            ('Timezone only fixed', len(timezone_only), 'SUCCESS'),
            ('Midnight only fixed', len(midnight_only), 'SUCCESS'),
            ('Midnight and timezone fixed', len(fully_fixed), 'SUCCESS'),
            ('Timezone fix error', len(tz_fix_failed), 'ERROR'),
            ('Midnight fix error', len(mc_fix_failed), 'ERROR'),
            ('Sanity check failed', len(sanity_failed), 'ERROR'),
        ]
        col_w = max(len(label) for label, _, _ in rows)
        for label, count, style in rows:
            line = f'\t{label:<{col_w}}  {count}'
            if style:
                line = getattr(self.style, style)(line)
            self.stdout.write(line)

        self.stdout.write('\n=========================================================')

    def _fix_run(self, run) -> RunResult:
        """
        Apply both correction stages to one run following the pipeline:

          0. Unable-to-process checks (no data, null timestamps).
          1. Stage 1: midnight-crossing fix.
          2. Stage 2: timezone normalization.
          3. Sanity check.

        The run's own timestamps are ground truth and never modified.

        Transaction model: Stage 1 and Stage 2 each run inside their own
        transaction.atomic() block. Both are wrapped in an outer
        transaction.atomic() that is rolled back on a SanityError from the
        final sanity check, reverting changes from both stages. A Stage 1
        fix error stops the pipeline; its inner savepoint is automatically
        rolled back, so no database writes from Stage 1 persist. A Stage 2
        fix error leaves Stage 1 changes intact unless the final sanity
        check triggers a full rollback.
        """
        run_results = TestIterationResult.objects.filter(test_run_id=run.pk).order_by(
            'exec_seqno',
            'pk',
        )

        # --- 0. Unable-to-process checks ---

        if not run_results.exists():
            self._log('info', 'skipping: no data')
            return RunResult(
                skipped_reason='no_data',
                midnight_fix_status=None,
                timezone_fix_status=None,
                error_msg=None,
            )

        packages = list(
            run_results.filter(parent_package_id=None).order_by('exec_seqno', 'pk'),
        )
        if not packages:
            return RunResult(
                skipped_reason='no_data',
                midnight_fix_status=None,
                timezone_fix_status=None,
                error_msg=None,
            )

        run_has_null = run.start is None or run.finish is None
        results_have_null = run_results.filter(
            Q(start__isnull=True) | Q(finish__isnull=True),
        ).exists()

        if run_has_null or results_have_null:
            self._log('info', 'skipping: not all timestamps are set')
            return RunResult(
                skipped_reason='null_timestamps',
                midnight_fix_status=None,
                timezone_fix_status=None,
                error_msg=None,
            )

        self._log('info', 'run boundaries', f'[{run.start}, {run.finish}]')
        for pkg in packages:
            self._log(
                'info',
                'pkg boundaries',
                f'[{pkg.start}, {pkg.finish}] ({pkg.iteration.test.name})',
            )

        # --- 1. Fix ---

        with transaction.atomic():
            try:
                # --- Stage 1: midnight-crossing fix ---
                tirs_changed: set[int] = set()
                try:
                    with transaction.atomic():
                        run_results_list, ch_map, packages = _collect_run_data(run, run_results)

                        _fix_run_tree(run, ch_map, tirs_changed)

                        for pkg in packages:
                            self._log(
                                'warning' if tirs_changed else 'info',
                                'pkg boundaries after date fix',
                                f'[{pkg.start}, {pkg.finish}] ({pkg.iteration.test.name}; '
                                f'{len(tirs_changed)} iteration(s) changed)',
                                style=self.style.WARNING if tirs_changed else None,
                            )

                        if tirs_changed:
                            run_results_by_id = {tir.pk: tir for tir in run_results_list}
                            changed_objects = [run_results_by_id[pk] for pk in tirs_changed]
                            TestIterationResult.objects.bulk_update(
                                changed_objects,
                                fields=['start', 'finish'],
                            )

                except Exception as e:
                    return RunResult(
                        skipped_reason=None,
                        midnight_fix_status='failed_fix_error',
                        timezone_fix_status=None,
                        error_msg=str(e),
                    )

                # --- Stage 2: timezone normalization ---
                delta: timedelta = timedelta(0)
                try:
                    with transaction.atomic():
                        # Reload in-memory list and rebuild the map after bulk update
                        run_results_list, ch_map, packages = _collect_run_data(run, run_results)

                        first_pkg = packages[0]
                        last_pkg = packages[-1]

                        delta_start = _get_delta_start(run.start, first_pkg.start)
                        delta_finish = _get_delta_finish(run.finish, last_pkg.finish)
                        delta = min((delta_start, delta_finish), key=abs)

                        if delta:
                            # Normalize timezone
                            run_results.update(
                                start=F('start') - delta,
                                finish=F('finish') - delta,
                            )
                            # Reload in-memory list and rebuild the map after bulk update
                            run_results_list, ch_map, packages = _collect_run_data(
                                run,
                                run_results,
                            )

                        for pkg in packages:
                            self._log(
                                'warning' if delta else 'info',
                                'pkg boundaries after timezone fix',
                                f'[{pkg.start}, {pkg.finish}] '
                                f'({pkg.iteration.test.name}; delta={delta})',
                                style=self.style.WARNING if delta else None,
                            )

                except Exception as e:
                    return RunResult(
                        skipped_reason=None,
                        midnight_fix_status='fixed' if tirs_changed else 'not_needed',
                        timezone_fix_status='failed_fix_error',
                        error_msg=str(e),
                    )

                if not _check_run_boundaries(run, packages):
                    msg = 'packages not compatible with run boundaries'
                    raise SanityError(msg)

            except SanityError as se:
                transaction.set_rollback(True)
                return RunResult(
                    skipped_reason=None,
                    midnight_fix_status='failed_sanity',
                    timezone_fix_status='failed_sanity',
                    error_msg=str(se),
                )

        if not delta and not tirs_changed:
            return RunResult(
                skipped_reason=None,
                midnight_fix_status='not_needed',
                timezone_fix_status='not_needed',
                error_msg=None,
            )

        return RunResult(
            skipped_reason=None,
            midnight_fix_status='fixed' if tirs_changed else 'not_needed',
            timezone_fix_status='fixed' if delta else 'not_needed',
            error_msg=None,
        )
