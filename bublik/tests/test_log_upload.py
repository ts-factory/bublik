# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import io
import json
import os
import shutil
import stat
import tarfile
import tempfile
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
    storage_subpath,
)
from bublik.core.logs_storage import LogsStorageError, storage_for_base
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
SUBPATH = 'sockapi-ts/2026/09/23/bench1-10:11:12'


def fs_base(root):
    return {
        'name': 'TE log server',
        'uri': ['https://logs.example/logs/'],
        'storage': {'type': 'fs', 'root': root, 'upload': True},
    }


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


def bundle_files(**extra):
    """The files of a bundle made outside TE, with pages and attachments already made."""
    return {
        'meta_data.json': meta_data(),
        'bublik.json': b'{}',
        'json/node_1_0.json': b'{}',
        'attachments/node_id5/attachments.json': b'{}',
        'attachments/node_id5/test-failed-1.png': b'png',
        'attachments/node_id5/video.webm': b'webm',
        '.done': b'',
        **extra,
    }


def directory(name):
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    return info


class OpenBundleTarTest(SimpleTestCase):
    def assert_invalid(self, fileobj, fragment):
        with self.assertRaises(UploadError) as caught:
            open_log_tar(fileobj)
        self.assertIn(fragment, caught.exception.message)

    def test_valid_bundle(self):
        tar = make_tar(bundle_files(), extra=[directory('json'), directory('attachments')])
        log_tar = open_log_tar(tar)
        self.assertEqual(log_tar.subpath, 'sockapi-ts/2026/09/23/bench1-10:11:12')
        # Directory entries are dropped: only files are stored.
        self.assertEqual(sorted(m.name for m in log_tar.members), sorted(bundle_files()))

    def test_unsafe_paths_are_rejected(self):
        for name in ('json/../../evil', './meta_data.json', 'json//x.json', '/etc/x'):
            with self.subTest(name=name):
                tar = make_tar(bundle_files(**{name: b'x'}))
                self.assert_invalid(tar, 'plain relative path')

    def test_nested_files_outside_the_bundle_dirs_are_rejected(self):
        self.assert_invalid(make_tar(bundle_files(**{'html/x.html': b'x'})), 'must lie under')
        self.assert_invalid(make_tar(bundle_files(), extra=[directory('html')]), 'directories')

    def test_extra_top_level_files_are_rejected(self):
        self.assert_invalid(make_tar(bundle_files(**{'notes.md': b'x'})), 'top level')

    def test_symlinks_are_rejected(self):
        link = tarfile.TarInfo('json/node_2.json')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        self.assert_invalid(make_tar(bundle_files(), extra=[link]), 'regular files')

    def test_missing_meta_data_is_rejected(self):
        files = bundle_files()
        del files['meta_data.json']
        self.assert_invalid(make_tar(files), 'meta_data.json')

    def test_bad_meta_data_is_rejected(self):
        files = bundle_files(**{'meta_data.json': meta_data(version=2)})
        self.assert_invalid(make_tar(files), 'version')

    def test_names_may_be_longer_than_the_log_servers(self):
        preview = 'ui-okt-limonikas-tanstack-table-v9'
        files = bundle_files(**{'meta_data.json': meta_data({**METAS, 'CFG': preview})})
        self.assertEqual(
            open_log_tar(make_tar(files)).subpath,
            f'sockapi-ts/2026/09/23/{preview}-10:11:12',
        )
        # A TE tarball keeps the log server's limit.
        te_files = {
            'meta_data.json': meta_data({**METAS, 'CFG': preview}),
            'raw_log_bundle.tpxz': b'',
        }
        self.assert_invalid(make_tar(te_files), 'invalid meta CFG')
        too_long = bundle_files(**{'meta_data.json': meta_data({**METAS, 'CFG': 'x' * 65})})
        self.assert_invalid(make_tar(too_long), 'invalid meta CFG')


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

    @mock.patch('bublik.core.s3.upload_bytes')
    @mock.patch('bublik.core.s3.upload_fileobj')
    @mock.patch('bublik.core.s3.object_exists', new=mock.Mock(return_value=False))
    def test_stores_a_bundle_as_it_is(self, upload_fileobj, upload_bytes):
        upload.publish_log_tar(open_log_tar(make_tar(bundle_files())), self.storage)
        prefix = 'uploads/sockapi-ts/2026/09/23/bench1-10:11:12/'
        stored = {c.args[0]: c.args[2] for c in upload_fileobj.call_args_list}
        self.assertEqual(
            sorted(stored),
            sorted(prefix + name for name in bundle_files() if name != '.done'),
        )
        self.assertEqual(stored[prefix + 'json/node_1_0.json'], 'application/json')
        self.assertEqual(stored[prefix + 'attachments/node_id5/test-failed-1.png'], 'image/png')
        self.assertEqual(stored[prefix + 'attachments/node_id5/video.webm'], 'video/webm')
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


class PublishLogTarToDirectoryTest(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.storage = storage_for_base(fs_base(self.root))
        self.run_dir = os.path.join(self.root, SUBPATH)

    def listing(self):
        found = []
        for directory, _dirs, files in os.walk(self.run_dir):
            found += [
                os.path.relpath(os.path.join(directory, name), self.run_dir) for name in files
            ]
        return sorted(found)

    def test_unpacks_the_run_like_the_log_server(self):
        url = upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        self.assertEqual(url, f'https://logs.example/logs/{SUBPATH}/')
        self.assertEqual(self.listing(), ['.done', 'meta_data.json', 'raw_log_bundle.tpxz'])
        with open(os.path.join(self.run_dir, 'raw_log_bundle.tpxz'), 'rb') as bundle:
            self.assertEqual(bundle.read(), b'bundle')
        # The web server generates json/ and html/ into the run directory.
        mode = stat.S_IMODE(os.stat(self.run_dir).st_mode)
        self.assertEqual(mode & stat.S_IRWXG, stat.S_IRWXG)

    def test_unpacks_a_bundle_with_its_directories(self):
        upload.publish_log_tar(open_log_tar(make_tar(bundle_files())), self.storage)
        self.assertEqual(self.listing(), sorted(bundle_files()))

    def test_already_uploaded(self):
        upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        with self.assertRaises(AlreadyUploadedError) as caught:
            upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        self.assertIn(f'https://logs.example/logs/{SUBPATH}/', caught.exception.message)
        self.assertIn('force=true', caught.exception.message)

    def test_an_upload_that_failed_midway_is_replaced(self):
        os.makedirs(self.run_dir)
        with open(os.path.join(self.run_dir, 'leftover'), 'wb') as leftover:
            leftover.write(b'x')
        upload.publish_log_tar(open_log_tar(make_tar()), self.storage)
        self.assertEqual(self.listing(), ['.done', 'meta_data.json', 'raw_log_bundle.tpxz'])

    def test_a_local_base_gives_the_run_a_file_url(self):
        base = {'name': 'L', 'uri': [f'file://{self.root}/'], 'storage': {'type': 'local'}}
        url = upload.publish_log_tar(open_log_tar(make_tar()), storage_for_base(base))
        self.assertEqual(url, f'file://{self.root}/{SUBPATH}/')
        self.assertEqual(self.listing(), ['.done', 'meta_data.json', 'raw_log_bundle.tpxz'])

    def test_a_missing_root_is_an_error_not_a_new_directory(self):
        storage = storage_for_base(fs_base(os.path.join(self.root, 'nope')))
        with self.assertRaises(LogsStorageError) as caught:
            upload.publish_log_tar(open_log_tar(make_tar()), storage)
        self.assertIn('nope', caught.exception.message)
        self.assertFalse(os.path.exists(os.path.join(self.root, 'nope')))


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

    def test_upload_into_a_log_server_directory(self):
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root)
        self.mocks['logs_bases'].return_value = [fs_base(root)]
        self.login(self.admin)
        response = self.post(project=self.project.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        url = f'https://logs.example/logs/{SUBPATH}/'
        self.assertEqual(response.data['url'], url)
        self.assertEqual(self.mocks['schedule_runs'].call_args.kwargs['url'], url)
        self.assertTrue(os.path.isfile(os.path.join(root, SUBPATH, '.done')))
        self.mocks['upload_fileobj'].assert_not_called()
        self.assertEqual(
            self.post(project=self.project.id).status_code, status.HTTP_409_CONFLICT
        )
