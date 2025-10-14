# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from argparse import ArgumentTypeError
from datetime import datetime
import json
from urllib.parse import urlsplit

from bublik.core.run.filter_expression import TestRunMeta


def parser_type_date(s):
    for fmt in ['%Y', '%Y.%m', '%Y.%m.%d']:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass

    msg = f'Invalid date format: {s}'
    raise ArgumentTypeError(msg)


def parser_type_url(s):
    u = urlsplit(s)

    if not u[0].startswith('http'):
        msg = f'Invalid URL format: {s}'
        raise ArgumentTypeError(msg)

    return s


def parser_type_force(s):
    s = json.loads(s.lower())
    if not isinstance(s, bool):
        msg = f'Invalid force parameter format: {s}'
        raise ArgumentTypeError(msg)

    return s


def parser_type_tags(param_tags):
    if not param_tags:
        return None

    param_tags_a = []
    param_tags = param_tags.split(',')
    for param_tag in [t for t in param_tags if t]:
        if '=' in param_tag:
            k, v = param_tag.split('=', 1)
            param_tags_a.append(TestRunMeta(k.strip(), v.strip()))
        else:
            param_tags_a.append(TestRunMeta(param_tag.strip()))

    return param_tags_a


def parser_type_str_or_none(s):
    if s.lower() == 'none':
        return None
    return str(s)
