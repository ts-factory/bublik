# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import json
import os
import shutil
import subprocess
import tempfile
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from bublik.core import logs_storage
from bublik.core.exceptions import BadGatewayError
from bublik.core.log import local_logs
from bublik.core.log.s3_logs import LogGenerationError, browser_url
from bublik.core.log.services import LogService
from bublik.core.logs_storage import storage_for_base


RUN = 'ts/2026/09/23/cfg-10:00:00'


def local_base(root):
    return {'name': 'Local', 'uri': [f'file://{root}/'], 'storage': {'type': 'local'}}


def fake_generator():
    """A stand-in for rgt-log-get-item writing a page, or a whole json/ or html/."""

    def run(command, **_kwargs):
        req_path = command[2].removeprefix('--req-path=')
        if os.path.basename(req_path) in ('json', 'html'):
            os.makedirs(os.path.join(req_path, 'images'), exist_ok=True)
            req_path = os.path.join(req_path, 'node_1_0.json')
        os.makedirs(os.path.dirname(req_path), exist_ok=True)
        with open(req_path, 'w') as f:
            f.write('{"generated": true}')
        return subprocess.CompletedProcess(command, 0, '', '')

    return mock.patch('subprocess.run', side_effect=run)


@override_settings(BUBLIK_FQDN='', URL_PREFIX='')
class BrowserUrlTest(SimpleTestCase):
    def test_file_urls_point_at_bublik(self):
        self.assertEqual(
            browser_url(f'file:///srv/logs/{RUN}/json/node_1_0.json'),
            f'/api/v2/logs/local/srv/logs/{RUN}/json/node_1_0.json'.replace(':', '%3A'),
        )
        self.assertEqual(
            browser_url('file:///srv/logs/run/', origin='https://bublik.example'),
            'https://bublik.example/api/v2/logs/local/srv/logs/run/',
        )

    @override_settings(URL_PREFIX='bublik')
    def test_prefix_and_normalization(self):
        self.assertEqual(
            browser_url('file:///srv//logs/./run/'), '/bublik/api/v2/logs/local/srv/logs/run/'
        )


class LocalRunMixin:
    def make_run(self, *files):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.run_dir = os.path.join(self.root, RUN)
        os.makedirs(self.run_dir)
        for name in files:
            with open(os.path.join(self.run_dir, name), 'w') as f:
                f.write('{}' if name.endswith('.json') else 'x')
        patcher = mock.patch.object(
            logs_storage, 'logs_bases', return_value=[local_base(self.root)]
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.storage = storage_for_base(local_base(self.root))


class OpenLocalItemTest(LocalRunMixin, TestCase):
    def setUp(self):
        self.make_run('meta_data.json', 'raw_log_bundle.tpxz')

    def read(self, path):
        item = local_logs.open_local_item(self.storage, path)
        if item is None:
            return None
        with item.stream:
            return item.stream.read()

    def test_existing_files_are_served_as_they_are(self):
        self.assertEqual(self.read(os.path.join(self.run_dir, 'meta_data.json')), b'{}')
        self.assertIsNone(self.read(os.path.join(self.run_dir, 'missing.txt')))

    def test_missing_pages_are_generated_next_to_the_bundle(self):
        page = os.path.join(self.run_dir, 'json', 'node_id5.json')
        with fake_generator() as run:
            self.assertEqual(self.read(page), b'{"generated": true}')
            # Served from disk from now on.
            self.assertEqual(self.read(page), b'{"generated": true}')
        run.assert_called_once()
        bundle = os.path.join(self.run_dir, 'raw_log_bundle.tpxz')
        self.assertEqual(run.call_args.args[0][1], f'--bundle={bundle}')
        self.assertTrue(os.path.isfile(page))

    def test_deep_items_generate_the_whole_directory(self):
        with fake_generator():
            self.assertEqual(
                self.read(os.path.join(self.run_dir, 'html', 'images', 'x.png')), None
            )
        # The directory was generated whole, which the fake marks with images/.
        self.assertTrue(os.path.isdir(os.path.join(self.run_dir, 'html', 'images')))
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, '.html-complete')))

    def test_a_run_without_a_bundle_generates_nothing(self):
        os.unlink(os.path.join(self.run_dir, 'raw_log_bundle.tpxz'))
        with mock.patch('subprocess.run') as run:
            self.assertIsNone(self.read(os.path.join(self.run_dir, 'json', 'node_id5.json')))
        run.assert_not_called()

    def test_generation_failures_are_reported(self):
        failed = subprocess.CompletedProcess([], 2, '', 'ERROR: broken bundle')
        with mock.patch('subprocess.run', return_value=failed):  # noqa: SIM117
            with self.assertRaises(LogGenerationError):
                self.read(os.path.join(self.run_dir, 'json', 'node_id5.json'))

    def test_listing_offers_the_pages_a_bundle_can_produce(self):
        directories, files = local_logs.list_local_directory(self.storage, self.run_dir)
        self.assertEqual(directories, ['html/', 'json/'])
        self.assertEqual(files, ['meta_data.json', 'raw_log_bundle.tpxz'])
        with fake_generator():
            directories, files = local_logs.list_local_directory(
                self.storage, os.path.join(self.run_dir, 'json')
            )
        self.assertEqual((directories, files), (['images/'], ['node_1_0.json']))
        # Bublik's own markers are not listed.
        directories, files = local_logs.list_local_directory(self.storage, self.run_dir)
        self.assertNotIn('.lock', files)
        self.assertNotIn('.json-complete', files)
        self.assertIsNone(local_logs.list_local_directory(self.storage, self.root + '/nope'))

    def test_only_paths_under_a_base_are_served(self):
        self.assertIsNotNone(local_logs.storage_for(self.run_dir))
        for path in (
            f'{self.root}/../etc/passwd',
            f'{self.root}/./x',
            '/etc/passwd',
            self.root + '-other/run',
            'relative/path',
        ):
            with self.subTest(path=path):
                self.assertIsNone(local_logs.storage_for(path))

    def test_log_service_reads_file_urls(self):
        with fake_generator():
            content = LogService.fetch_log_content(f'file://{self.run_dir}/json/node_id5.json')
            # Generating json/ whole does not produce this one.
            missing = LogService.fetch_log_content(f'file://{self.run_dir}/json/nope/x.json')
        self.assertEqual(content, {'generated': True})
        self.assertIsNone(missing)
        self.assertIsNone(LogService.fetch_log_content('file:///etc/passwd'))
        failed = subprocess.CompletedProcess([], 2, '', 'ERROR: broken bundle')
        with mock.patch('subprocess.run', return_value=failed):  # noqa: SIM117
            with self.assertRaises(BadGatewayError):
                LogService.fetch_log_content(f'file://{self.run_dir}/html/node_1_0.html')


@override_settings(URL_PREFIX='')
class LocalLogViewTest(LocalRunMixin, TestCase):
    def setUp(self):
        self.make_run('meta_data.json', 'raw_log_bundle.tpxz', 'trc-brief.html')

    def url(self, *parts):
        return f'/api/v2/logs/local{self.run_dir}/' + '/'.join(parts)

    def test_existing_files_are_streamed(self):
        response = self.client.get(self.url('meta_data.json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'{}')
        self.assertEqual(response['Content-Type'], 'application/json; charset=utf-8')
        self.assertEqual(response['Content-Length'], '2')

    def test_missing_pages_are_generated(self):
        with fake_generator():
            response = self.client.get(self.url('json', 'node_id5.json'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(b''.join(response.streaming_content)), {'generated': True})

    def test_generation_errors_are_503(self):
        failed = subprocess.CompletedProcess([], 2, '', 'ERROR: broken bundle')
        with mock.patch('subprocess.run', return_value=failed):
            response = self.client.get(self.url('json', 'node_id5.json'))
        self.assertEqual(response.status_code, 503)

    def test_directories_are_listed(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for name in ('json/', 'html/', 'meta_data.json', 'trc-brief.html'):
            self.assertIn(f'href="{name}"', body)
        self.assertNotIn('.done', body)

    def test_paths_outside_the_base_and_unsafe_ones_are_404(self):
        for url in (
            '/api/v2/logs/local/etc/passwd',
            f'/api/v2/logs/local{self.root}/../x',
            f'/api/v2/logs/local{self.root}-other/',
            self.url('nope.txt'),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_only_get_and_head(self):
        self.assertEqual(self.client.post(self.url('meta_data.json')).status_code, 405)
