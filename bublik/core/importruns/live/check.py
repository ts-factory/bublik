# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import timedelta
import logging

from bublik.core.cache import RunCache
from bublik.core.importruns import ImportMode
from bublik.core.run.objects import set_run_status
from bublik.data.models.result import TestIterationResult


logger = logging.getLogger()


def livelog_check_run_timeout(run):
    # To avoid cyclic dependency between DashboardView - this - LiveLogContext

    # No need to check a finished run
    if run.finish:
        return False

    # No need to check an offline run
    if run.import_mode != ImportMode.LIVE:
        return False

    # Try to fetch the context
    cache = RunCache.by_obj(run, 'livelog')
    ctx = cache.data

    # If it's not there, mark the run finished
    if not ctx:
        logger.info(f'no live import context for run {run.id}, cleaning up')

        # Finish pending tests and the run itself
        item_results = TestIterationResult.objects.filter(
            test_run=run,
            finish__isnull=True,
        ).order_by('start')

        last_ts = item_results[len(item_results) - 1].start if item_results else run.start

        delta = timedelta(microseconds=1)
        last_ts += delta

        for item in reversed(item_results):
            item.finish = last_ts
            last_ts += delta
            item.save()

        run.finish = last_ts
        run.save()

        set_run_status(run, 'RUN_STATUS_ERROR')
        return True

    return False
