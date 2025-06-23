# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime

from django.conf import settings
from django.utils import timezone
import pytz


'''
Use these (or create new for upcoming needs) functions
whenever you need any datetime formattings.

param ds: datetime string.
param dt: datetime object.
param date_str: date string.
'''


DISPLAY = settings.DATETIME_FORMATS['display']
DB_DATE_FORMAT = settings.DATETIME_FORMATS['db']
INPUT_DATE_FORMATS = settings.DATETIME_FORMATS['input']['date']


def to_db_format(ds):
    return datetime.strptime(ds, DB_DATE_FORMAT['default'])


def to_date(ds):
    return datetime.strptime(ds, DISPLAY['to_date_in_numbers'])


def date_str_to_date(ds):
    for parse_format in INPUT_DATE_FORMATS:
        try:
            return datetime.strptime(ds, parse_format).date()
        except ValueError:
            continue
    return None


def date_str_to_db(ds):
    dt = date_str_to_date(ds)
    return dt.strftime(DB_DATE_FORMAT['iso_date'])


def display_to_date_in_numbers(dt):
    return dt.strftime(DISPLAY['to_date_in_numbers']) if dt else None


def display_to_date_in_words(dt):
    return dt.strftime(DISPLAY['to_date_in_words']) if dt else None


def display_to_milliseconds(dt):
    return dt.strftime(DISPLAY['to_microseconds'])[:-3] if dt else None


def get_duration(dt_start, dt_finish):
    return str(dt_finish - dt_start)[:-3]


def utc_ts_to_dt(ts):
    return datetime.fromtimestamp(ts, timezone.get_current_timezone())


def localize_date(date):
    current_tz = timezone.get_current_timezone()
    if not isinstance(date, datetime):
        date = to_db_format(date)
    # No need to localize an aware datetime (may have been passed by user)
    if date.tzinfo is None or date.tzinfo.utcoffset(date) is None:
        date = pytz.timezone(str(current_tz)).localize(date)
    return date


def period_to_str(period):
    """
    Period represents a tuple of 2 datetime objects.
    To use it as an ID for html objects it should be a str without extra symbols.

    An example is bellow.

    input: (
        datetime.datetime(2020, 2, 26, 23, 10, 7, 271000),
        datetime.datetime(2020, 2, 26, 23, 25, 13, 437000)
    )

    output: "1582758607ms271-1582759513ms437"
    ('ms' is TIMESTAMP_DELIMITER defined in settings.py)

    Note: Even microseconds are important for grouping test results,
          so they shouldn't be just rounded.
    """

    def to_str(dt):
        if dt is None:
            return 'None'
        return str(dt.timestamp()).replace('.', settings.TIMESTAMP_DELIMITER)

    return f'{to_str(period[0])}{settings.PERIOD_DELIMITER}{to_str(period[1])}'


def parse_period(string):
    """
    This is an inverse function to 'period_to_str()'.
    """

    def to_timestamp(ds):
        if ds == 'None':
            return None
        return float(ds.replace(settings.TIMESTAMP_DELIMITER, '.'))

    def to_datetime(ts_str):
        ts = to_timestamp(ts_str)
        if ts is None:
            return None
        return datetime.fromtimestamp(ts)

    return tuple(map(to_datetime, string.split(settings.PERIOD_DELIMITER)))
