# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import codecs

from django.conf import settings
from rest_framework import parsers
from rest_framework.exceptions import ParseError
from rest_framework.utils import json


def _object_without_repeated_keys(pairs):
    """
    Build a JSON object from its key/value pairs, refusing a key that comes
    twice: `json` would keep its last value without telling anyone.
    """
    obj = {}
    for key, value in pairs:
        if key in obj:
            msg = f'Repeated key {key!r}'
            raise ValueError(msg)
        obj[key] = value
    return obj


class JSONParser(parsers.JSONParser):
    """
    DRF's JSON parser, except that a key repeated in an object, at any
    depth, is a parse error, as malformed JSON is, rather than the last of
    its values silently taking the place of the others.
    """

    def parse(self, stream, media_type=None, parser_context=None):
        parser_context = parser_context or {}
        encoding = parser_context.get('encoding', settings.DEFAULT_CHARSET)

        try:
            decoded_stream = codecs.getreader(encoding)(stream)
            parse_constant = json.strict_constant if self.strict else None
            return json.load(
                decoded_stream,
                parse_constant=parse_constant,
                object_pairs_hook=_object_without_repeated_keys,
            )
        except ValueError as exc:
            # Without the cause, as DRF's own parser raises it: this is a plain
            # 400, which the exception handler would otherwise log as an error.
            msg = f'JSON parse error - {exc}'
            raise ParseError(msg) from None
