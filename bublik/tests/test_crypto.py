# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import SimpleTestCase, override_settings

from bublik.core.crypto import DecryptionError, decrypt_json, encrypt_json


@override_settings(SECRET_KEY='deployment-secret')
class CryptoTest(SimpleTestCase):
    def test_roundtrip(self):
        token = encrypt_json({'Authorization': 'Bearer s3cret'})
        self.assertNotIn('s3cret', token)
        self.assertEqual(decrypt_json(token), {'Authorization': 'Bearer s3cret'})

    def test_garbage_raises_decryption_error(self):
        with self.assertRaises(DecryptionError):
            decrypt_json('not-a-token')

    def test_a_changed_secret_key_cannot_decrypt(self):
        token = encrypt_json(['x'])
        with override_settings(SECRET_KEY='other'), self.assertRaises(DecryptionError):
            decrypt_json(token)
