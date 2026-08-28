# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from datetime import date

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from bublik.core.datetime_formatting import parse_date_param


class ParseDateParamTest(SimpleTestCase):
    def test_accepted_formats(self):
        for value in ('2026-10-16', '2026/10/16', '2026.10.16'):
            assert parse_date_param(value) == date(2026, 10, 16), value

    def test_invalid_date_names_the_parameter(self):
        for value in ('2026-13-01', '16.10.2026', 'today'):
            with self.assertRaises(ValidationError) as context:
                parse_date_param(value, 'created_after')
            assert 'created_after' in context.exception.detail, value
