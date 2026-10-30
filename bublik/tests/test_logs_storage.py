# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import os
import shutil
import tempfile
from unittest import mock

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, TestCase, override_settings
from jsonschema import Draft7Validator

from bublik.core import logs_storage
from bublik.core.exceptions import RunCompromisedError
from bublik.core.importruns.source.run_traversal import S3PrefixTraverser, traverser_for
from bublik.core.importruns.source.storage import S3RunSource, run_source_for
from bublik.core.logs_storage import (
    FsLogsStorage,
    LogsStorageError,
    S3LogsStorage,
    find_s3_storage,
    storage_for_base,
)
from bublik.data.models import Project
from bublik.data.schemas.services import load_schema
from bublik.interfaces.api_v2.importruns.serializers import ImportrunsSerializer


S3_BASE = {
    'name': 'Uploads',
    'uri': ['s3://bublik-logs/uploads/'],
    'storage': {
        'type': 's3',
        'access_key': '${env:LOGS_S3_ACCESS_KEY}',
        'secret_key': '${env:LOGS_S3_SECRET_KEY}',
        'upload': True,
    },
}
HTTP_BASE = {'name': 'TE log server', 'uri': ['https://logs.example/logs/']}
FS_BASE = {
    'name': 'TE log server',
    'uri': ['https://logs.example/logs/'],
    'storage': {'type': 'fs', 'root': '/home/te-logs/logs', 'upload': True},
}
S3_ENV = {'LOGS_S3_ACCESS_KEY': 'uploader', 'LOGS_S3_SECRET_KEY': 'uploader-secret'}
S3_SETTINGS = {
    'S3_ENDPOINT_URL': 'http://127.0.0.1:8333',
    'S3_ACCESS_KEY': 'bublik',
    'S3_SECRET_KEY': 'bublik-secret-key',
    'S3_REGION': 'us-east-1',
    'S3_PUBLIC_ENDPOINT_URL': '',
}


def _client_error(code):
    return ClientError({'Error': {'Code': code}}, 'Op')


class ReferencesSchemaTest(SimpleTestCase):
    def setUp(self):
        self.validator = Draft7Validator(load_schema('references'))

    def errors(self, base):
        return list(self.validator.iter_errors({'LOGS_BASES': [base]}))

    def test_http_s3_and_fs_bases_are_valid(self):
        self.assertEqual(self.errors(HTTP_BASE), [])
        self.assertEqual(self.errors(S3_BASE), [])
        self.assertEqual(self.errors(FS_BASE), [])
        self.assertEqual(
            self.errors({'name': 'Local', 'uri': ['s3://b/p/'], 'storage': {'type': 's3'}}),
            [],
        )

    def test_fs_storage_needs_an_absolute_root_and_nothing_else(self):
        for storage in (
            {'type': 'fs'},
            {'type': 'fs', 'root': 'relative/logs'},
            {'type': 'fs', 'root': '/srv/logs', 'endpoint_url': 'http://127.0.0.1:8333'},
            {'type': 'fs', 'root': '/srv/logs', 'access_key': '${env:LOGS_KEY}'},
            {'type': 'nfs', 'root': '/srv/logs'},
        ):
            with self.subTest(storage=storage):
                self.assertNotEqual(self.errors({**FS_BASE, 'storage': storage}), [])

    def test_s3_storage_has_no_root(self):
        base = {**S3_BASE, 'storage': {**S3_BASE['storage'], 'root': '/srv/logs'}}
        self.assertNotEqual(self.errors(base), [])

    def test_plaintext_and_foreign_credentials_are_rejected(self):
        for secret in ('uploader-secret', '${env:AI_SECRET}', '${env:SECRET_KEY}'):
            with self.subTest(secret=secret):
                base = {**S3_BASE, 'storage': {**S3_BASE['storage'], 'secret_key': secret}}
                self.assertNotEqual(self.errors(base), [])

    def test_uri_scheme_must_match_the_storage(self):
        self.assertNotEqual(self.errors({**HTTP_BASE, 'uri': ['s3://b/p/']}), [])
        self.assertNotEqual(self.errors({**FS_BASE, 'uri': ['s3://b/p/']}), [])
        self.assertNotEqual(
            self.errors({**S3_BASE, 'uri': ['https://logs.example/logs/']}),
            [],
        )

    def test_unknown_storage_fields_are_rejected(self):
        base = {**S3_BASE, 'storage': {**S3_BASE['storage'], 'password': 'x'}}
        self.assertNotEqual(self.errors(base), [])


@override_settings(**S3_SETTINGS)
class StorageForBaseTest(SimpleTestCase):
    @mock.patch.dict('os.environ', S3_ENV)
    def test_resolves_credentials_and_falls_back_to_settings(self):
        storage = storage_for_base(S3_BASE)
        self.assertEqual(storage.target.bucket, 'bublik-logs')
        self.assertEqual(storage.target.endpoint_url, 'http://127.0.0.1:8333')
        self.assertEqual(storage.target.access_key, 'uploader')
        self.assertEqual(storage.target.secret_key, 'uploader-secret')
        self.assertTrue(storage.upload)
        self.assertEqual(storage.uri, 's3://bublik-logs/uploads/')

    def test_minimal_base_uses_the_s3_settings(self):
        storage = storage_for_base(
            {'name': 'L', 'uri': ['s3://b/p/'], 'storage': {'type': 's3'}}
        )
        self.assertEqual(storage.target.access_key, 'bublik')
        self.assertFalse(storage.upload)

    @mock.patch.dict('os.environ', {}, clear=True)
    def test_unresolved_credentials_are_an_error_not_a_fallback(self):
        with self.assertRaises(LogsStorageError) as caught:
            storage_for_base(S3_BASE)
        self.assertIn('access_key', caught.exception.message)
        self.assertNotIn('uploader', caught.exception.message)

    def test_keys_and_urls(self):
        storage = storage_for_base(
            {'name': 'L', 'uri': ['s3://b/p/'], 'storage': {'type': 's3'}}
        )
        self.assertIsInstance(storage, S3LogsStorage)
        self.assertEqual(storage.key('s3://b/p/run/'), 'p/run/')
        self.assertEqual(storage.url('p/run/'), 's3://b/p/run/')
        self.assertEqual(storage.run_url('ts/run'), 's3://b/p/ts/run/')

    def test_run_prefix_joins_the_base_prefix(self):
        for uri, prefix in (
            ('s3://b/uploads/', 'uploads/ts/run/'),
            ('s3://b/uploads', 'uploads/ts/run/'),
            ('s3://b/', 'ts/run/'),
        ):
            with self.subTest(uri=uri):
                base = {'name': 'U', 'uri': [uri], 'storage': {'type': 's3'}}
                self.assertEqual(storage_for_base(base).run_prefix('ts/run'), prefix)

    def test_fs_storage(self):
        storage = storage_for_base(FS_BASE)
        self.assertIsInstance(storage, FsLogsStorage)
        self.assertEqual(storage.root, '/home/te-logs/logs')
        self.assertEqual(storage.uri, 'https://logs.example/logs/')
        self.assertTrue(storage.upload)
        self.assertEqual(storage.run_dir('ts/run'), '/home/te-logs/logs/ts/run')
        # The run is imported and served through the log server, like any other.
        self.assertEqual(storage.run_url('ts/run'), 'https://logs.example/logs/ts/run/')

    def test_fs_root_must_be_absolute(self):
        base = {**FS_BASE, 'storage': {'type': 'fs', 'root': 'logs'}}
        with self.assertRaises(LogsStorageError):
            storage_for_base(base)

    def test_unknown_storage_type(self):
        base = {**FS_BASE, 'storage': {'type': 'nfs'}}
        with self.assertRaises(LogsStorageError):
            storage_for_base(base)


@override_settings(**S3_SETTINGS)
@mock.patch.dict('os.environ', S3_ENV)
class FindStorageTest(SimpleTestCase):
    @mock.patch.object(
        logs_storage, 'logs_bases', new=mock.Mock(return_value=[HTTP_BASE, S3_BASE])
    )
    def test_matches_only_s3_bases_by_prefix(self):
        self.assertEqual(
            find_s3_storage('s3://bublik-logs/uploads/ts/run/', 1).base,
            S3_BASE,
        )
        self.assertIsNone(find_s3_storage('s3://bublik-logs/elsewhere/', 1))
        self.assertIsNone(find_s3_storage('https://logs.example/logs/run/', 1))

    @mock.patch.object(
        logs_storage, 'logs_bases', new=mock.Mock(return_value=[HTTP_BASE, S3_BASE])
    )
    def test_upload_storage(self):
        self.assertEqual(logs_storage.upload_storage_for(1).base, S3_BASE)

    @mock.patch.object(
        logs_storage, 'logs_bases', new=mock.Mock(return_value=[FS_BASE, S3_BASE])
    )
    def test_fs_upload_storage(self):
        storage = logs_storage.upload_storage_for(1)
        self.assertIsInstance(storage, FsLogsStorage)
        self.assertEqual(storage.base, FS_BASE)
        # Log serving from S3 does not mistake an fs base for one of its own.
        self.assertIsNone(find_s3_storage('https://logs.example/logs/run/', 1))

    @mock.patch.object(logs_storage, 'logs_bases', new=mock.Mock(return_value=[HTTP_BASE]))
    def test_no_upload_storage_by_default(self):
        self.assertIsNone(logs_storage.upload_storage_for(1))


@override_settings(**S3_SETTINGS)
@mock.patch.dict('os.environ', S3_ENV)
class S3RunSourceTest(SimpleTestCase):
    url = 's3://bublik-logs/uploads/ts/2026/09/23/cfg-10:00:00'

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def source(self):
        return S3RunSource(self.url, storage_for_base(S3_BASE))

    def test_normalizes_the_run_prefix(self):
        source = self.source()
        self.assertEqual(source.prefix, 'uploads/ts/2026/09/23/cfg-10:00:00/')
        self.assertEqual(source.location('bublik.json'), self.url + '/bublik.json')

    @mock.patch('bublik.core.s3.download_file')
    def test_fetch(self, download_file):
        self.assertTrue(self.source().fetch_to_dir('meta_data.json', self.dir))
        key, path = download_file.call_args.args
        self.assertEqual(key, 'uploads/ts/2026/09/23/cfg-10:00:00/meta_data.json')
        self.assertEqual(path, os.path.join(self.dir, 'meta_data.json'))
        self.assertEqual(download_file.call_args.kwargs['target'].bucket, 'bublik-logs')

    @mock.patch('bublik.core.s3.download_file')
    def test_fetch_miss_leaves_no_file(self, download_file):
        def fail(_key, path, **_kwargs):
            open(path, 'wb').close()
            raise _client_error(code='404')

        download_file.side_effect = fail
        self.assertFalse(self.source().fetch_to_dir('bublik.json', self.dir))
        self.assertEqual(os.listdir(self.dir), [])

    @mock.patch('bublik.core.s3.download_file', new=mock.Mock(side_effect=_client_error('403')))
    def test_fetch_other_errors_propagate(self):
        with self.assertRaises(ClientError):
            self.source().fetch_to_dir('bublik.json', self.dir)

    @mock.patch('bublik.core.s3.object_exists', side_effect=[True, False])
    def test_exists(self, object_exists):
        source = self.source()
        self.assertTrue(source.exists('.done'))
        self.assertFalse(source.exists('missing'))
        self.assertEqual(
            object_exists.call_args_list[0].args,
            ('uploads/ts/2026/09/23/cfg-10:00:00/.done',),
        )

    @mock.patch(
        'bublik.core.importruns.source.storage.find_s3_storage',
        new=mock.Mock(return_value=None),
    )
    def test_url_outside_every_s3_base_is_an_import_error(self):
        with self.assertRaises(LogsStorageError):
            run_source_for('s3://other/run/', 1)


@override_settings(**S3_SETTINGS)
@mock.patch.dict('os.environ', S3_ENV)
@mock.patch('bublik.core.importruns.source.run_traversal.create_event')
@mock.patch('bublik.core.importruns.source.run_traversal.get_import_job_task')
@mock.patch.object(logs_storage, 'logs_bases', return_value=[S3_BASE])
class S3PrefixTraverserTest(SimpleTestCase):
    def runs(self, url, existing, prefixes):
        def object_exists(key, **_kwargs):
            return key in existing

        def list_prefixes(prefix, **_kwargs):
            return prefixes.get(prefix, [])

        with mock.patch('bublik.core.s3.object_exists', side_effect=object_exists):  # noqa: SIM117
            with mock.patch('bublik.core.s3.list_prefixes', side_effect=list_prefixes):
                return list(S3PrefixTraverser(url, 1, 1).find_runs())

    def test_a_run_prefix_is_one_run(self, *_mocks):
        runs = self.runs(
            's3://bublik-logs/uploads/ts/run/',
            {'uploads/ts/run/meta_data.json'},
            {},
        )
        self.assertEqual(runs, [('s3://bublik-logs/uploads/ts/run/', None)])

    def test_a_parent_prefix_yields_every_run_beneath_it(self, *_mocks):
        runs = self.runs(
            's3://bublik-logs/uploads/',
            {'uploads/ts/a/meta_data.json', 'uploads/ts/b/meta_data.json'},
            {'uploads/': ['uploads/ts/'], 'uploads/ts/': ['uploads/ts/a/', 'uploads/ts/b/']},
        )
        self.assertEqual(
            [url for url, error in runs if error is None],
            ['s3://bublik-logs/uploads/ts/a/', 's3://bublik-logs/uploads/ts/b/'],
        )

    def test_compromised_runs_are_skipped(self, *_mocks):
        runs = self.runs(
            's3://bublik-logs/uploads/',
            {'uploads/bad/trc_compromised.js', 'uploads/bad/meta_data.json'},
            {'uploads/': ['uploads/bad/']},
        )
        self.assertEqual(len(runs), 1)
        self.assertIsInstance(runs[0][1], RunCompromisedError)


class S3TraverserSelectionTest(TestCase):
    @override_settings(**S3_SETTINGS)
    @mock.patch.dict('os.environ', S3_ENV)
    @mock.patch(
        'bublik.core.importruns.source.run_traversal.get_import_job_task', new=mock.Mock()
    )
    @mock.patch.object(logs_storage, 'logs_bases', return_value=[S3_BASE])
    def test_s3_urls_use_the_prefix_traverser(self, logs_bases):
        project = Project.objects.create(name='uploads-project')
        traverser = traverser_for('s3://bublik-logs/uploads/', 1, project.name)
        self.assertIsInstance(traverser, S3PrefixTraverser)
        logs_bases.assert_called_with(project.id)


class ImportUrlValidationTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='uploads-project')

    def validate(self, url):
        serializer = ImportrunsSerializer(data={'url': url, 'project': self.project.id})
        return serializer.is_valid(), serializer.errors

    @mock.patch(
        'bublik.interfaces.api_v2.importruns.serializers.ConfigServices.getattr_from_global',
        new=mock.Mock(return_value=[HTTP_BASE, S3_BASE]),
    )
    def test_urls_under_a_configured_base_are_accepted(self):
        self.assertTrue(self.validate('s3://bublik-logs/uploads/ts/run/')[0])
        self.assertTrue(self.validate('https://logs.example/logs/ts/run/')[0])

    @mock.patch(
        'bublik.interfaces.api_v2.importruns.serializers.ConfigServices.getattr_from_global',
        new=mock.Mock(return_value=[HTTP_BASE, S3_BASE]),
    )
    def test_other_urls_are_rejected(self):
        for url in ('s3://other-bucket/run/', 's3:///no-bucket', 'ftp://logs.example/logs/'):
            with self.subTest(url=url):
                valid, errors = self.validate(url)
                self.assertFalse(valid)
                self.assertIn('url', errors)
