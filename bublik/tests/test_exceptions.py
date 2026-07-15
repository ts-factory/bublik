# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase

from bublik.core.exceptions import normalize_error_details


class NormalizeErrorDetailsTest(SimpleTestCase):
    def test_flat_errors(self):
        assert normalize_error_details({'title': ['Required.'], 'state': 'Bad.'}) == {
            'title': ['Required.'],
            'state': ['Bad.'],
        }

    def test_nested_errors_stay_structured(self):
        assert normalize_error_details(
            {'issue': {'title': ['Taken.'], 'bug_key': {'tracker': ['Unknown.']}}},
        ) == {'issue': {'title': ['Taken.'], 'bug_key': {'tracker': ['Unknown.']}}}

    def test_detail(self):
        assert normalize_error_details({'detail': 'Not found.'}) == ['Not found.']
