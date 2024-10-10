# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import copy
import itertools
import logging
import os

from django.conf import settings

from bublik.data.models import EventLog


logger = logging.getLogger('bublik.server')


class Counter:
    """It's used in context to templates for counting."""

    counter = 0

    def increment(self):
        self.counter += 1
        return self.counter


def key_value_transforming(key, value, delim=settings.KEY_VALUE_DELIMITER):
    if key and value:
        try:
            return key + delim + value
        except TypeError:
            return str(key) + delim + str(value)
    elif not value:
        return key
    else:
        return value


def key_value_list_transforming(items, delim=settings.KEY_VALUE_DELIMITER):
    for item in items:
        if isinstance(item, dict):
            if all(k in item for k in ('name', 'value')):
                '''Example: item = { 'name': n, 'value': v }.'''
                key = item.get('name')
                value = item.get('value')
            elif 'value' in item:
                '''Example: item = { 'value': v }.'''
                key = item.get('value')
                value = None
            else:
                '''Example: item = { n: v }.'''
                key, value = item.popitem()

        elif isinstance(item, tuple):
            '''Example: item = ( n, v ).'''
            key, value = item

        else:
            msg = "Can't resolve an item type."
            raise TypeError(msg)

        yield key_value_transforming(key, value, delim)


def key_value_dict_transforming(items, delim=settings.KEY_VALUE_DELIMITER):
    if isinstance(items, dict):
        for key, value in items.items():
            yield key_value_transforming(key, value, delim)
    else:
        msg = "Can't resolve an item type."
        raise TypeError(msg)


def find_dict_in_list(search, dicts):
    for item in dicts:
        if all(k in item and item[k] == v for k, v in search.items()):
            return item
    return None


def get_difference(sublist, baselist, ignore=None):
    if ignore is None:
        ignore = []
    matched = set(sublist)
    if ignore:
        matched = set(sublist) - set(ignore)
    return matched - (set(sublist) & set(baselist))


def choices_keys_as_values(values):
    return tuple(zip(values, values))


def empty_to_none(data, fields):
    if isinstance(data, dict):
        data = copy.deepcopy(data)
        for field in fields:
            if not data.get(field):
                data[field] = None
    return data


def search_files_in_sub_dirs(basedir, filename):
    for dirpath, _dirs, files in os.walk(basedir):
        if filename in files:
            yield os.path.join(dirpath, filename)


def get_local_log(filename, basedir=settings.MANAGEMENT_COMMANDS_LOG):
    '''
    Returns absolute path to the log or raises RunTimeError if
    no one or more than one log file was found.
    '''

    files = list(search_files_in_sub_dirs(basedir, filename))

    if not files:
        msg = f"Log file {filename} wasn't found in {basedir} and its nested dirs"
        raise RuntimeError(msg)

    if len(files) > 1:
        msg = f'Found more than one log file for run: {files}'
        raise RuntimeError(msg)

    return files[0]


def get_multiplier(multiplier):
    try:
        return float(multiplier)
    except ValueError:
        degree = multiplier.split('p')
        return int(degree[0], 16) * 2 ** float(degree[1])


def apply_multiplier(value, multiplier):
    return value * get_multiplier(multiplier)


def get_readable_value(value, multiplier, base_units):
    return str(apply_multiplier(value, multiplier)) + ' ' + base_units


def get_metric_prefix_units(multiplier, base_units):
    prefixes = {
        '1e-9': ['nano', 'n'],
        '1e-6': ['micro', 'μ'],
        '1e-3': ['milli', 'm'],
        '1': ['plain', ''],
        '1e+3': ['kilo', 'k'],
        '1x1p10': ['kibi', 'Ki'],
        '1e+6': ['mega', 'M'],
        '0x1p20': ['mebi', 'Mi'],
        '1e+9': ['giga', 'G'],
        '0x1p30': ['gibi', 'Gi'],
    }
    return f'{prefixes[multiplier][1]}{base_units}'


def isnumeric_int(value):
    try:
        int(value)
    except ValueError:
        return False
    else:
        return True


def dicts_groupby(iterable, dict_key):
    def without_key(data):
        for d in data:
            d.pop(dict_key)
            yield d

    def key_fn(x):
        return x[dict_key]

    iterable = sorted(iterable, key=key_fn)
    for key, data in itertools.groupby(iterable, key_fn):
        yield key, list(without_key(data))


def get_same_key_values(test_list, key, extra_condition=None):

    # getting keys
    keys = list(test_list[0].keys())

    res = []

    if extra_condition:
        test_list = filter(extra_condition, test_list)

    # iterating each dictionary for similar key's value
    for key in keys:

        # using all to check all keys with similar values
        flag = all(test_list[0][key] == ele[key] for ele in test_list)

        if flag:
            res.append(key)

    return res


def create_event(facility, severity, msg):
    event = EventLog.objects.create(
        facility=facility,
        severity=severity,
        msg=msg,
    )
    logger.debug(
        'created Event object: '
        f'pk={event.pk}, '
        f'timestamp={event.timestamp}, '
        f'facility={event.facility}, '
        f'severity={event.severity}, '
        f'msg={event.msg}',
    )
