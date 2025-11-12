# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime, timedelta, timezone

from bublik.core.datetime_formatting import date_str_to_date, localize_date
from bublik.core.logging import get_task_or_server_logger
from bublik.data import models


logger = get_task_or_server_logger()


def prepare_date(date_str):
    try:
        return localize_date(date_str)
    except (ValueError, TypeError):
        logger.error(f'unexpected date format encountered: {type(date_str)} ({date_str})')
        return None


def prepare_dates_period(get_start=None, get_finish=None, delta_days=7):
    """
    This function calculates the period depending on the specified start and finish dates.
    If both @get_start and @get_start were not passed, rely on that run can not last more
    than a day, search the latest run by 'start' and have the minimum value from
    (its start + one day) and now as he upper bound of the period,
    then the bottom bound will be the upper - @delta_days.

    NB! This is more accurate to search by 'finish', but runs with no finish can be
    not only the freshest, but also broken ones. Such runs starting long time ago
    can fail at the start and technically not finished.
    """

    start = None
    finish = None
    need_limit = False

    if not get_start and not get_finish:
        latest_run = (
            models.TestIterationResult.objects.filter(test_run=None).order_by('-start').first()
        )

        finish = datetime.now(timezone.utc).date()
        if latest_run:
            finish = min((latest_run.start + timedelta(days=1)).date(), finish)
        start = finish - timedelta(days=delta_days)
        need_limit = True

    elif not get_start and get_finish:
        start = date_str_to_date(get_finish) - timedelta(days=delta_days)
        finish = date_str_to_date(get_finish)
        need_limit = True

    elif get_start and not get_finish:
        start = date_str_to_date(get_start)
        finish = datetime.now().date()
        need_limit = False

    else:
        start = date_str_to_date(get_start)
        finish = date_str_to_date(get_finish)
        need_limit = False

    return start, finish, need_limit
