# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from unittest import mock

from django.test import SimpleTestCase, override_settings

from bublik.core.secrets import resolve_secret_reference


class SecretReferenceTests(SimpleTestCase):
    @mock.patch.dict('os.environ', {'LOGS_S3_SECRET_KEY': 'env-secret'})
    def test_resolves_name_under_the_given_prefix(self):
        self.assertEqual(
            resolve_secret_reference('${env:LOGS_S3_SECRET_KEY}', 'LOGS_'),
            'env-secret',
        )

    @override_settings(LOGS_S3_SECRET_KEY='settings-secret')
    def test_resolves_settings_source(self):
        self.assertEqual(
            resolve_secret_reference('${settings:LOGS_S3_SECRET_KEY}', 'LOGS_'),
            'settings-secret',
        )

    @mock.patch.dict('os.environ', {'LOGS_S3_SECRET_KEY': 'env-secret'})
    def test_name_outside_the_prefix_is_unresolved(self):
        self.assertIsNone(resolve_secret_reference('${env:LOGS_S3_SECRET_KEY}', 'AI_'))

    @mock.patch.dict('os.environ', {'SECRET_KEY': 'django-secret'})
    def test_bare_setting_names_are_never_dereferenced(self):
        for prefix in ('AI_', 'LOGS_'):
            with self.subTest(prefix=prefix):
                self.assertIsNone(resolve_secret_reference('${env:SECRET_KEY}', prefix))

    @override_settings(LOGS_EMPTY='', LOGS_NUMBER=1)
    def test_missing_empty_and_non_string_values_are_unresolved(self):
        for reference in (
            None,
            '',
            '${settings:LOGS_MISSING}',
            '${settings:LOGS_EMPTY}',
            '${settings:LOGS_NUMBER}',
            'plain-secret',
            '${vault:LOGS_X}',
        ):
            with self.subTest(reference=reference):
                self.assertIsNone(resolve_secret_reference(reference, 'LOGS_'))

    def test_invalid_prefix_is_rejected(self):
        for prefix in ('', 'ai_', 'AI', '.*'):
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                resolve_secret_reference('${env:AI_X}', prefix)
