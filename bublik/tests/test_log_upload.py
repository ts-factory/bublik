# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import io
import json
import tarfile
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.test.utils import override_settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from bublik.core import logs_storage
from bublik.core.importruns import upload
from bublik.core.importruns.upload import (
    AlreadyUploadedError,
    UploadError,
    open_log_tar,
    run_prefix,
    storage_subpath,
)
from bublik.core.logs_storage import storage_for_base
from bublik.data.models import EventLog, Project, User, UserRoles, UserToken


METAS = {
    'CAMPAIGN_DATE': '2026-09-23',
    'TS_NAME': 'sockapi-ts',
    'CFG': 'bench1',
    'START_TIMESTAMP': '2026-09-23T10:11:12+03:00',
    'PROJECT': 'uploads-project',
}
UPLOAD_BASE = {
    'name': 'Uploads',
    'uri': ['s3://bublik-logs/uploads/'],
    'storage': {'type': 's3', 'upload': True},
}
UPLOAD_URL = '/api/v2/importruns/upload/'


def meta_data(metas=None, version=1):
    metas = METAS if metas is None else metas
    return json.dumps(
        {'version': version, 'metas': [{'name': k, 'value': v} for k, v in metas.items()]},
    ).encode()


def make_tar(files=None, *, extra=()):
    """A tar with ``files`` ({name: bytes}) plus ``extra`` raw TarInfo members."""
    if files is None:
        files = {'meta_data.json': meta_data(), 'raw_log_bundle.tpxz': b'bundle'}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info in extra:
            tar.addfile(info)
    buffer.seek(0)
    return buffer


class OpenLogTarTest(SimpleTestCase):
    def assert_invalid(self, fileobj, fragment):
        with self.assertRaises(UploadError) as caught:
            open_log_tar(fileobj)
        self.assertIn(fragment, caught.exception.message)

    def test_valid_tar(self):
        log_tar = open_log_tar(make_tar())
        self.assertEqual(log_tar.subpath, 'sockapi-ts/2026/09/23/bench1-10:11:12')
        self.assertEqual(log_tar.project_name, 'uploads-project')
        self.assertEqual(
            sorted(m.name for m in log_tar.members),
            ['meta_data.json', 'raw_log_bundle.tpxz'],
        )

    def test_optional_files_are_allowed(self):
        files = {
            'meta_data.json': meta_data(),
            'raw_log_bundle.tpxz': b'bundle',
            'trc-brief.html': b'<html/>',
            'trc-stats.txt': b'stats',
            '.done': b'',
        }
        self.assertEqual(len(open_log_tar(make_tar(files)).members), 5)

    def test_not_a_tar(self):
        self.assert_invalid(io.BytesIO(b'not a tar at all' * 100), 'uncompressed tar')

    def test_compressed_tar_is_rejected(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
            info = tarfile.TarInfo('meta_data.json')
            tar.addfile(info, io.BytesIO(b''))
        buffer.seek(0)
        self.assert_invalid(buffer, 'uncompressed tar')

    def test_path_traversal_is_rejected(self):
        files = {'meta_data.json': meta_data(), 'raw_log_bundle.tpxz': b'b', '../evil': b'x'}
        self.assert_invalid(make_tar(files), 'top level')

    def test_nested_paths_are_rejected(self):
        files = {'meta_data.json': meta_data(), 'raw_log_bundle.tpxz': b'b', 'run/x.txt': b'x'}
        self.assert_invalid(make_tar(files), 'top level')

    def test_symlinks_and_directories_are_rejected(self):
        link = tarfile.TarInfo('trc-brief.html')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        directory = tarfile.TarInfo('trc-stats.txt')
        directory.type = tarfile.DIRTYPE
        for member in (link, directory):
            with self.subTest(type=member.type):
                files = {'meta_data.json': meta_data(), 'raw_log_bundle.tpxz': b'b'}
                self.assert_invalid(make_tar(files, extra=[member]), 'regular files')

    def test_extra_files_are_rejected(self):
        files = {'meta_data.json': meta_data(), 'raw_log_bundle.tpxz': b'b', 'notes.md': b'x'}
        self.assert_invalid(make_tar(files), 'not allowed: notes.md')

    def test_missing_bundle_is_rejected(self):
        self.assert_invalid(make_tar({'meta_data.json': meta_data()}), 'raw_log_bundle.tpxz')

    def test_bad_meta_data(self):
        cases = {
            'version': meta_data(version=2),
            'valid JSON': b'{nope',
            'JSON object': b'[]',
            'missing meta CFG': meta_data({k: v for k, v in METAS.items() if k != 'CFG'}),
            'invalid meta CFG': meta_data({**METAS, 'CFG': 'has space'}),
            'invalid meta CAMPAIGN_DATE': meta_data({**METAS, 'CAMPAIGN_DATE': '2026-13-01'}),
            'START_TIMESTAMP': meta_data({**METAS, 'START_TIMESTAMP': 'yesterday'}),
        }
        for fragment, data in cases.items():
            with self.subTest(fragment=fragment):
                files = {'meta_data.json': data, 'raw_log_bundle.tpxz': b'b'}
                self.assert_invalid(make_tar(files), fragment)


class StorageSubpathTest(SimpleTestCase):
    def test_matches_the_log_server_layout(self):
        cases = {
            '2026-09-23T10:11:12+03:00': 'bench1-10:11:12',
            '2026-09-23T10:11:12Z': 'bench1-10:11:12',
            '2026-09-23T10:11:12.345+00:00': 'bench1-10:11:12',
            '10:11:12': 'bench1-10:11:12',
        }
        for timestamp, leaf in cases.items():
            with self.subTest(timestamp=timestamp):
                subpath = storage_subpath({**METAS, 'START_TIMESTAMP': timestamp})
                self.assertEqual(subpath, f'sockapi-ts/2026/09/23/{leaf}')

    def test_user_is_part_of_the_leaf(self):
        subpath = storage_subpath({**METAS, 'USER': 'ci'})
        self.assertEqual(subpath, 'sockapi-ts/2026/09/23/bench1-ci-10:11:12')

    def test_values_are_matched_whole(self):
        # A trailing newline (a shell's $(...) gone wrong) would end up in the key.
        for name in ('CFG', 'TS_NAME', 'USER', 'CAMPAIGN_DATE'):
            with self.subTest(name=name), self.assertRaises(UploadError):
                storage_subpath({**METAS, name: METAS.get(name, 'ci') + '\n'})

    def test_run_prefix_joins_the_base_prefix(self):
        for uri, prefix in (
            ('s3://b/uploads/', 'uploads/ts/run/'),
            ('s3://b/uploads', 'uploads/ts/run/'),
            ('s3://b/', 'ts/run/'),
        ):
            with self.subTest(uri=uri):
                base = {'name': 'U', 'uri': [uri], 'storage': {'type': 's3'}}
                self.assertEqual(run_prefix(storage_for_base(base), 'ts/run'), prefix)


class PublishLogTarTest(SimpleTestCase):
    def setUp(self):
        self.storage = storage_for_base(UPLOAD_BASE)

    @mock.patch('bublik.core.s3.upload_bytes')
    @mock.patch('bublik.core.s3.upload_fileobj')
    @mock.patch('bublik.core.s3.object_exists', new=mock.Mock(return_value=False))
    def test_stores_every_file_and_the_done_marker_last(self, upload_fileobj, upload_bytes):
        url = upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        prefix = 'uploads/sockapi-ts/2026/09/23/bench1-10:11:12/'
        self.assertEqual(url, f's3://bublik-logs/{prefix}')
        self.assertEqual(
            sorted(c.args[0] for c in upload_fileobj.call_args_list),
            [prefix + 'meta_data.json', prefix + 'raw_log_bundle.tpxz'],
        )
        upload_bytes.assert_called_once()
        self.assertEqual(upload_bytes.call_args.args[0], prefix + '.done')

    @mock.patch('bublik.core.s3.upload_fileobj')
    @mock.patch('bublik.core.s3.object_exists')
    def test_already_uploaded(self, object_exists, upload_fileobj):
        object_exists.side_effect = lambda key, **_: key.endswith('/.done')
        with self.assertRaises(AlreadyUploadedError) as caught:
            upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        self.assertIn('force=true', caught.exception.message)
        upload_fileobj.assert_not_called()

    @mock.patch('bublik.core.s3.upload_bytes')
    @mock.patch('bublik.core.s3.upload_fileobj')
    @mock.patch('bublik.core.s3.object_exists')
    def test_an_upload_that_failed_midway_is_written_over(
        self, object_exists, upload_fileobj, upload_bytes
    ):
        # meta_data.json went up, the bundle did not, so there is no .done.
        object_exists.side_effect = lambda key, **_: key.endswith('/meta_data.json')
        upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        self.assertEqual(upload_fileobj.call_count, 2)
        upload_bytes.assert_called_once()


@override_settings(
    CACHES={
        'default': {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'},
        'run': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
        'project': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    },
)
class UploadApiTest(APITestCase):
    def setUp(self):
        self.project = Project.objects.create(name='uploads-project')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )
        self.user = User.objects.create_user(email='user@example.com', password='pw12345!')

        patches = {
            'logs_bases': mock.patch.object(
                logs_storage, 'logs_bases', return_value=[UPLOAD_BASE]
            ),
            'exists': mock.patch('bublik.core.s3.object_exists', return_value=False),
            'upload_fileobj': mock.patch('bublik.core.s3.upload_fileobj'),
            'upload_bytes': mock.patch('bublik.core.s3.upload_bytes'),
            'schedule_runs': mock.patch(
                'bublik.interfaces.api_v2.importruns.views.schedule_runs',
                return_value=[{'run_source_url': 'x', 'celery_task_id': 'task-1'}],
            ),
        }
        self.mocks = {name: patcher.start() for name, patcher in patches.items()}
        for patcher in patches.values():
            self.addCleanup(patcher.stop)

    def post(self, tar=None, **fields):
        data = {'file': SimpleUploadedFile('te_logs.tar', (tar or make_tar()).getvalue())}
        data.update(fields)
        return self.client.post(UPLOAD_URL, data, format='multipart')

    def login(self, user):
        self.client.cookies['access_token'] = str(AccessToken.for_user(user))

    def test_admin_with_a_personal_access_token_can_upload(self):
        _, raw_token = UserToken.objects.issue(self.admin, 'ci')
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {raw_token}')
        response = self.post(project=self.project.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        url = 's3://bublik-logs/uploads/sockapi-ts/2026/09/23/bench1-10:11:12/'
        self.assertEqual(response.data['url'], url)
        self.assertIn('job_id', response.data)
        kwargs = self.mocks['schedule_runs'].call_args.kwargs
        self.assertEqual(kwargs['url'], url)
        self.assertEqual(kwargs['project_name'], 'uploads-project')
        self.assertFalse(kwargs['force'])

    def test_project_defaults_to_the_project_meta(self):
        self.login(self.admin)
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            self.mocks['schedule_runs'].call_args.kwargs['project_name'],
            'uploads-project',
        )

    def test_force_is_passed_to_the_import(self):
        self.login(self.admin)
        self.post(project=self.project.id, force='true')
        self.assertTrue(self.mocks['schedule_runs'].call_args.kwargs['force'])

    def test_anonymous_and_non_admin_callers_are_refused(self):
        self.assertEqual(self.post().status_code, status.HTTP_403_FORBIDDEN)
        self.login(self.user)
        self.assertEqual(self.post().status_code, status.HTTP_403_FORBIDDEN)
        self.mocks['upload_fileobj'].assert_not_called()

    def test_project_without_an_upload_base_is_refused(self):
        self.mocks['logs_bases'].return_value = [
            {'name': 'TE', 'uri': ['https://logs.example/logs/']},
        ]
        self.login(self.admin)
        response = self.post(project=self.project.id)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('"upload": true', response.data['message'])
        self.mocks['upload_fileobj'].assert_not_called()

    def test_unknown_projects_are_refused(self):
        self.login(self.admin)
        self.assertEqual(self.post(project=999999).status_code, status.HTTP_400_BAD_REQUEST)
        tar = make_tar(
            {
                'meta_data.json': meta_data({**METAS, 'PROJECT': 'nope'}),
                'raw_log_bundle.tpxz': b'b',
            },
        )
        response = self.post(tar)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("'nope'", response.data['message'])

    def test_invalid_tar_is_refused_and_recorded(self):
        self.login(self.admin)
        response = self.post(make_tar({'meta_data.json': meta_data()}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('raw_log_bundle.tpxz', response.data['message'])
        event = EventLog.objects.get()
        self.assertIn('rejected upload te_logs.tar', event.msg)
        self.assertEqual(event.severity, EventLog.SeverityChoices.ERR)

    def test_repeated_upload_conflicts(self):
        self.mocks['exists'].return_value = True
        self.login(self.admin)
        response = self.post(project=self.project.id)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.mocks['schedule_runs'].assert_not_called()

    @override_settings(IMPORT_UPLOAD_MAX_SIZE=100)
    def test_oversized_upload(self):
        self.login(self.admin)
        response = self.post(project=self.project.id)
        self.assertEqual(response.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)
        self.mocks['upload_fileobj'].assert_not_called()
