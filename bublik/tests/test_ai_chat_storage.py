# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Unit tests for the chat file storage seam and its local-disk backend."""

import os
from pathlib import Path
import stat
import tempfile
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from bublik.core import ai_chat_storage, local_storage, s3


class LocalRootTest(SimpleTestCase):
    @override_settings(AI_CHAT_FILE_STORAGE_DIR='')
    def test_unset_storage_dir_is_an_actionable_error(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            local_storage.root()
        self.assertIn('AI_CHAT_FILE_STORAGE_DIR', str(caught.exception))
        self.assertIn('AI_CHAT_FILE_STORAGE_BACKEND', str(caught.exception))


class LocalStorageTest(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / 'chat-files'
        overridden = override_settings(
            AI_CHAT_FILE_STORAGE_BACKEND='local',
            AI_CHAT_FILE_STORAGE_DIR=str(self.root),
        )
        overridden.enable()
        self.addCleanup(overridden.disable)

    def test_round_trip(self):
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'report.pdf')
        ai_chat_storage.upload_bytes(key, b'payload', 'application/pdf')

        self.assertEqual(ai_chat_storage.read_object(key), b'payload')
        self.assertTrue((self.root / 'chat/thread-1/file-1/report.pdf').is_file())

    def test_overwriting_a_key_replaces_the_content(self):
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'report.txt')
        ai_chat_storage.upload_bytes(key, b'first', 'text/plain')
        ai_chat_storage.upload_bytes(key, b'second', 'text/plain')

        self.assertEqual(ai_chat_storage.read_object(key), b'second')

    def test_upload_leaves_no_temporary_files_behind(self):
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'report.txt')
        ai_chat_storage.upload_bytes(key, b'payload', 'text/plain')

        directory = self.root / 'chat/thread-1/file-1'
        self.assertEqual([p.name for p in directory.iterdir()], ['report.txt'])

    def test_stored_files_and_directories_are_private(self):
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'report.txt')
        ai_chat_storage.upload_bytes(key, b'payload', 'text/plain')

        path = self.root / 'chat/thread-1/file-1/report.txt'
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_delete_prefix_removes_only_that_thread(self):
        mine = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'mine.txt')
        theirs = ai_chat_storage.chat_file_key('thread-2', 'file-2', 'theirs.txt')
        ai_chat_storage.upload_bytes(mine, b'mine', 'text/plain')
        ai_chat_storage.upload_bytes(theirs, b'theirs', 'text/plain')

        ai_chat_storage.delete_prefix(ai_chat_storage.thread_prefix('thread-1'))

        self.assertFalse((self.root / 'chat/thread-1').exists())
        self.assertEqual(ai_chat_storage.read_object(theirs), b'theirs')

    def test_delete_prefix_prunes_empty_parents(self):
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'only.txt')
        ai_chat_storage.upload_bytes(key, b'only', 'text/plain')

        ai_chat_storage.delete_prefix(ai_chat_storage.thread_prefix('thread-1'))

        self.assertFalse((self.root / 'chat').exists())
        self.assertTrue(self.root.exists())

    def test_delete_prefix_of_a_thread_without_files_is_harmless(self):
        ai_chat_storage.delete_prefix(ai_chat_storage.thread_prefix('never-used'))

    def test_delete_prefix_refuses_to_wipe_the_root(self):
        with self.assertRaises(ValueError):
            ai_chat_storage.delete_prefix('')

    def test_traversal_keys_are_rejected(self):
        for key in ('../escaped.txt', 'chat/../../escaped.txt', '/etc/passwd'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                ai_chat_storage.read_object(key)

    def test_traversal_keys_are_rejected_on_upload(self):
        with self.assertRaises(ValueError):
            ai_chat_storage.upload_bytes('../escaped.txt', b'x', 'text/plain')
        self.assertFalse((Path(self._tmp.name) / 'escaped.txt').exists())

    def test_local_files_are_always_proxied(self):
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'report.txt')
        self.assertIsNone(ai_chat_storage.public_download_url(key, 'report.txt'))

    def test_existing_directory_permissions_are_left_alone(self):
        self.root.mkdir(parents=True)
        os.chmod(self.root, 0o750)
        key = ai_chat_storage.chat_file_key('thread-1', 'file-1', 'report.txt')

        ai_chat_storage.upload_bytes(key, b'payload', 'text/plain')

        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o750)


class BackendSelectionTest(SimpleTestCase):
    @override_settings(AI_CHAT_FILE_STORAGE_BACKEND='local')
    def test_local_backend_selects_local_disk(self):
        self.assertFalse(ai_chat_storage.use_s3())
        self.assertIs(ai_chat_storage._backend(), local_storage)

    @override_settings(
        AI_CHAT_FILE_STORAGE_BACKEND='s3',
        S3_ENDPOINT_URL='http://127.0.0.1:8333',
    )
    def test_s3_backend_selects_s3(self):
        self.assertTrue(ai_chat_storage.use_s3())
        self.assertIs(ai_chat_storage._backend(), s3)

    @override_settings(
        AI_CHAT_FILE_STORAGE_BACKEND='local',
        S3_ENDPOINT_URL='http://127.0.0.1:8333',
    )
    def test_the_switch_wins_over_a_configured_endpoint(self):
        # An endpoint left over from an earlier configuration must not quietly
        # override the backend the deployment asked for.
        self.assertIs(ai_chat_storage._backend(), local_storage)

    @override_settings(AI_CHAT_FILE_STORAGE_BACKEND='  S3  ')
    def test_the_switch_is_case_and_whitespace_insensitive(self):
        with override_settings(S3_ENDPOINT_URL='http://127.0.0.1:8333'):
            self.assertEqual(ai_chat_storage.backend_name(), 's3')

    @override_settings(AI_CHAT_FILE_STORAGE_BACKEND='s3', S3_ENDPOINT_URL='')
    def test_s3_without_an_endpoint_is_an_actionable_error(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            ai_chat_storage.backend_name()
        self.assertIn('AI_CHAT_FILE_STORAGE_BACKEND', str(caught.exception))
        self.assertIn('S3_ENDPOINT_URL', str(caught.exception))

    @override_settings(AI_CHAT_FILE_STORAGE_BACKEND='seaweedfs')
    def test_an_unknown_backend_is_an_actionable_error(self):
        with self.assertRaises(ImproperlyConfigured) as caught:
            ai_chat_storage.backend_name()
        self.assertIn('AI_CHAT_FILE_STORAGE_BACKEND', str(caught.exception))
        self.assertIn('local', str(caught.exception))
        self.assertIn('s3', str(caught.exception))

    @override_settings(
        AI_CHAT_FILE_STORAGE_BACKEND='s3',
        S3_ENDPOINT_URL='http://127.0.0.1:8333',
    )
    def test_s3_dispatch_does_not_touch_the_local_disk(self):
        with mock.patch('bublik.core.s3.upload_bytes') as upload:
            ai_chat_storage.upload_bytes('chat/t/f/name.txt', b'x', 'text/plain')
        upload.assert_called_once_with('chat/t/f/name.txt', b'x', 'text/plain')

    @override_settings(AI_CHAT_FILE_STORAGE_BACKEND='', S3_ENDPOINT_URL='')
    def test_an_absent_switch_falls_back_to_the_old_inference(self):
        # A settings.py generated before the switch existed keeps behaving the
        # way it was configured: the endpoint is what selected object storage.
        self.assertEqual(ai_chat_storage.backend_name(), 'local')
        with override_settings(S3_ENDPOINT_URL='http://127.0.0.1:8333'):
            self.assertEqual(ai_chat_storage.backend_name(), 's3')

    def test_missing_settings_fall_back_to_the_documented_defaults(self):
        # A settings.py generated before a setting existed must degrade rather
        # than raise AttributeError.
        with mock.patch('bublik.core.ai_chat_storage.settings', object()):
            self.assertEqual(ai_chat_storage.setting('S3_ENDPOINT_URL'), '')
            self.assertEqual(ai_chat_storage.setting('S3_BUCKET'), 'bublik-ai-chat-files')


class S3PublicUrlTest(SimpleTestCase):
    @override_settings(
        AI_CHAT_FILE_STORAGE_BACKEND='s3',
        S3_ENDPOINT_URL='http://127.0.0.1:8333',
        S3_PUBLIC_ENDPOINT_URL='',
    )
    def test_loopback_s3_is_proxied_rather_than_redirected(self):
        self.assertIsNone(ai_chat_storage.public_download_url('chat/t/f/a.txt', 'a.txt'))

    @override_settings(
        AI_CHAT_FILE_STORAGE_BACKEND='s3',
        S3_ENDPOINT_URL='http://127.0.0.1:8333',
        S3_PUBLIC_ENDPOINT_URL='https://s3.example.com',
        S3_BUCKET='bublik-ai-chat-files',
    )
    def test_public_endpoint_yields_a_presigned_url(self):
        url = ai_chat_storage.public_download_url('chat/t/f/a.txt', 'a.txt')

        self.assertIsNotNone(url)
        self.assertTrue(url.startswith('https://s3.example.com/bublik-ai-chat-files/'))
        self.assertIn('X-Amz-Signature', url)
