# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from bublik.core import logs_storage
from bublik.core.log import s3_logs
from bublik.core.log.s3_logs import browser_url, generate_item, split_generated
from bublik.core.log.services import LogService
from bublik.core.logs_storage import storage_for_base


S3_BASE = {'name': 'Uploads', 'uri': ['s3://bublik-logs/uploads/'], 'storage': {'type': 's3'}}
HTTP_BASE = {'name': 'TE', 'uri': ['https://logs.example/logs/']}
RUN = 'uploads/ts/2026/09/23/cfg-10:00:00/'


class BrowserUrlTest(SimpleTestCase):
    @override_settings(URL_PREFIX='bublik')
    def test_s3_urls_point_at_bublik(self):
        self.assertEqual(
            browser_url(f's3://bublik-logs/{RUN}json/node_1_0.json'),
            f'/bublik/api/v2/logs/s3/bublik-logs/{RUN}json/node_1_0.json'.replace(':', '%3A'),
        )

    @override_settings(URL_PREFIX='')
    def test_origin_and_empty_prefix(self):
        self.assertEqual(
            browser_url('s3://b/run/', origin='https://bublik.example/'),
            'https://bublik.example/api/v2/logs/s3/b/run/',
        )
        self.assertEqual(browser_url('s3://b/run/'), '/api/v2/logs/s3/b/run/')

    def test_other_urls_are_unchanged(self):
        self.assertEqual(
            browser_url('https://logs.example/logs/run/'), 'https://logs.example/logs/run/'
        )
        self.assertIsNone(browser_url(None))


class SplitGeneratedTest(SimpleTestCase):
    def test_generated_items(self):
        self.assertEqual(
            split_generated(f'{RUN}json/node_id5.json'),
            (RUN, 'json/node_id5.json'),
        )
        self.assertEqual(
            split_generated(f'{RUN}html/node_1_0.html'), (RUN, 'html/node_1_0.html')
        )

    def test_other_and_unsafe_keys(self):
        for key in (
            f'{RUN}meta_data.json',
            f'{RUN}../json/x.json',
            f'{RUN}json/..',
            'uploads//json/x.json',
        ):
            with self.subTest(key=key):
                self.assertIsNone(split_generated(key))


class GenerationTest(SimpleTestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cache)
        override = override_settings(LOGS_S3_CACHE_DIR=self.cache)
        override.enable()
        self.addCleanup(override.disable)
        self.storage = storage_for_base(S3_BASE)

        def download_file(_key, path, **_kwargs):
            with open(path, 'wb') as f:
                f.write(b'bundle')

        patches = [
            mock.patch('bublik.core.s3.object_exists', return_value=True),
            mock.patch('bublik.core.s3.download_file', side_effect=download_file),
        ]
        self.mocks = [patcher.start() for patcher in patches]
        for patcher in patches:
            self.addCleanup(patcher.stop)
        self.download_file = self.mocks[1]

    def fake_generator(self, delay=0.0):
        def run(command, **_kwargs):
            time.sleep(delay)
            req_path = command[2].removeprefix('--req-path=')
            os.makedirs(os.path.dirname(req_path), exist_ok=True)
            with open(req_path, 'w') as f:
                f.write('{"generated": true}')
            return subprocess.CompletedProcess(command, 0, '', '')

        return mock.patch('subprocess.run', side_effect=run)

    def test_generates_into_the_cache(self):
        with self.fake_generator() as run:
            path = generate_item(self.storage, RUN, 'json/node_id5.json')
        self.assertTrue(path.startswith(self.cache))
        command = run.call_args.args[0]
        self.assertEqual(command[0], 'rgt-log-get-item')
        self.assertTrue(command[1].endswith(f'{RUN}raw_log_bundle.tpxz'))
        self.download_file.assert_called_once()

    def test_concurrent_requests_generate_once(self):
        results = []
        with self.fake_generator(delay=0.2) as run:
            threads = [
                threading.Thread(
                    target=lambda: results.append(
                        generate_item(self.storage, RUN, 'json/node_id5.json'),
                    ),
                )
                for _ in range(4)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(self.download_file.call_count, 1)
        self.assertEqual(len(set(results)), 1)

    def test_no_bundle_means_nothing_to_generate(self):
        self.mocks[0].return_value = False
        with self.fake_generator() as run:
            self.assertIsNone(generate_item(self.storage, RUN, 'json/node_id5.json'))
        run.assert_not_called()

    def test_generator_failure_is_an_error(self):
        failed = subprocess.CompletedProcess([], 2, '', 'ERROR: broken bundle')
        with mock.patch('subprocess.run', return_value=failed):  # noqa: SIM117
            with self.assertRaises(s3_logs.LogGenerationError):
                generate_item(self.storage, RUN, 'json/node_id5.json')


@override_settings(URL_PREFIX='')
@mock.patch.object(logs_storage, 'logs_bases', new=mock.Mock(return_value=[HTTP_BASE, S3_BASE]))
class S3LogViewTest(TestCase):
    url = f'/api/v2/logs/s3/bublik-logs/{RUN}json/node_id5.json'

    @mock.patch('bublik.core.s3.open_object')
    def test_existing_objects_are_streamed(self, open_object):
        open_object.return_value = {
            'Body': io.BytesIO(b'{"a": 1}'),
            'ContentType': 'application/json',
            'ContentLength': 8,
        }
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'{"a": 1}')
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(open_object.call_args.args[0], f'{RUN}json/node_id5.json')

    @mock.patch('bublik.core.s3.upload_fileobj')
    @mock.patch('bublik.core.s3.open_object', new=mock.Mock(return_value=None))
    def test_missing_pages_are_generated_and_written_back(self, upload_fileobj):
        generated = tempfile.NamedTemporaryFile(delete=False, suffix='.json')  # noqa: SIM115
        generated.write(b'{"generated": true}')
        generated.close()
        self.addCleanup(os.unlink, generated.name)
        with mock.patch.object(
            s3_logs, 'generate_item', return_value=generated.name
        ) as generate:
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'{"generated": true}')
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[1:], (RUN, 'json/node_id5.json'))
        self.assertEqual(upload_fileobj.call_args.args[0], f'{RUN}json/node_id5.json')

    @mock.patch('bublik.core.s3.open_object', new=mock.Mock(return_value=None))
    def test_generation_failure_is_503(self):
        error = s3_logs.LogGenerationError(message='failed to generate node_id5.json')
        with mock.patch.object(s3_logs, 'generate_item', side_effect=error):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)

    @mock.patch('bublik.core.s3.open_object', new=mock.Mock(return_value=None))
    def test_missing_non_page_is_404(self):
        response = self.client.get(f'/api/v2/logs/s3/bublik-logs/{RUN}trc-brief.html')
        self.assertEqual(response.status_code, 404)

    @mock.patch('bublik.core.s3.open_object')
    def test_keys_outside_the_configured_base_are_404(self, open_object):
        for url in (
            '/api/v2/logs/s3/bublik-logs/elsewhere/x.json',
            '/api/v2/logs/s3/other-bucket/uploads/x.json',
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        open_object.assert_not_called()

    @mock.patch(
        'bublik.core.s3.list_entries',
        new=mock.Mock(return_value=([], [f'{RUN}meta_data.json', f'{RUN}raw_log_bundle.tpxz'])),
    )
    def test_run_directory_listing_offers_the_pages(self):
        response = self.client.get(f'/api/v2/logs/s3/bublik-logs/{RUN}')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        for name in ('json/', 'html/', 'meta_data.json', 'raw_log_bundle.tpxz'):
            self.assertIn(f'href="{name}"', body)

    def test_writes_are_not_allowed(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)


@override_settings(SECURE_HTTP=True, URL_PREFIX='', ENABLE_JSON_LOGS_PROXY=True)
class JsonLogUrlsTest(SimpleTestCase):
    def urls(self, source, origin):
        result = mock.Mock(test_run=object(), exec_seqno=5)
        services = 'bublik.core.log.services'
        with mock.patch(f'{services}.ResultService.get_result', return_value=result):  # noqa: SIM117
            with mock.patch(f'{services}.get_sources', return_value=source):
                return LogService.get_json_log_urls(1, None, origin)

    def test_s3_runs_get_bublik_urls_and_skip_the_proxy(self):
        urls = self.urls('s3://b/run/', 'bublik.example')
        self.assertEqual(
            urls['url'], 'https://bublik.example/api/v2/logs/s3/b/run/json/node_id5.json'
        )
        self.assertTrue(
            urls['attachments_url'].startswith('https://bublik.example/api/v2/logs/s3/')
        )

    def test_server_side_callers_keep_s3_urls(self):
        self.assertEqual(self.urls('s3://b/run/', None)['url'], 's3://b/run/json/node_id5.json')

    def test_log_server_runs_are_unchanged(self):
        urls = self.urls('https://logs.example/logs/run/', 'bublik.example')
        self.assertIn(
            '/api/v2/logs/proxy/?url=https://logs.example/logs/run/json/', urls['url']
        )
