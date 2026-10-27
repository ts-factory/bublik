# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import io
import os
import shutil
import subprocess
import tempfile
import threading
import time
from types import MappingProxyType
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from bublik.core import logs_storage
from bublik.core.log import s3_logs
from bublik.core.log.s3_logs import browser_url, generate_item, split_generated
from bublik.core.log.services import LogService
from bublik.core.logs_storage import storage_for_base


S3_BASE = {'name': 'Uploads', 'uri': ['s3://bublik-logs/uploads/'], 'storage': {'type': 's3'}}
HTTP_BASE = {'name': 'TE', 'uri': ['https://logs.example/logs/']}
RUN = 'uploads/ts/2026/09/23/cfg-10:00:00/'


@override_settings(BUBLIK_FQDN='')
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

    @override_settings(URL_PREFIX='bublik', BUBLIK_FQDN='https://bublik.example:8080/')
    def test_the_fqdn_makes_it_absolute_for_callers_without_a_request(self):
        self.assertEqual(
            browser_url('s3://b/run/'),
            'https://bublik.example:8080/bublik/api/v2/logs/s3/b/run/',
        )
        # The request's own origin is what the client can reach.
        self.assertEqual(
            browser_url('s3://b/run/', origin='http://10.0.0.1'),
            'http://10.0.0.1/bublik/api/v2/logs/s3/b/run/',
        )


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
            if os.path.basename(req_path) in ('json', 'html'):
                # The whole directory.
                os.makedirs(os.path.join(req_path, 'images'), exist_ok=True)
                req_path = os.path.join(req_path, 'node_1_0.json')
            os.makedirs(os.path.dirname(req_path), exist_ok=True)
            with open(req_path, 'w') as f:
                f.write('{"generated": true}')
            return subprocess.CompletedProcess(command, 0, '', '')

        return mock.patch('subprocess.run', side_effect=run)

    def run_dir(self, run=RUN):
        return os.path.join(self.cache, 'bublik-logs', *run.split('/'))

    def age(self, run_dir, seconds):
        """Make the folder look last used ``seconds`` ago."""
        then = time.time() - seconds
        os.utime(os.path.join(run_dir, '.lock'), (then, then))

    def test_using_a_page_marks_the_folder_used(self):
        with self.fake_generator():
            generate_item(self.storage, RUN, 'json/node_id5.json')
            self.age(self.run_dir(), 7200)
            generate_item(self.storage, RUN, 'json/node_id5.json')
        lock = os.path.join(self.run_dir(), '.lock')
        self.assertLess(time.time() - os.stat(lock).st_mtime, 60)

    def test_prune_deletes_only_folders_unused_for_the_ttl(self):
        other = RUN.replace('cfg-10', 'cfg-11')
        with self.fake_generator():
            generate_item(self.storage, RUN, 'json/node_id5.json')
            generate_item(self.storage, other, 'json/node_id5.json')
        self.age(self.run_dir(), 7200)
        self.assertEqual(s3_logs.prune_cache(), 1)
        self.assertFalse(os.path.exists(self.run_dir()))
        self.assertTrue(os.path.isdir(self.run_dir(other)))
        # Its now empty parents go too, the cache itself stays.
        with self.fake_generator():
            generate_item(self.storage, 'uploads/x/', 'json/node_id5.json')
        self.age(self.run_dir(other), 7200)
        self.age(self.run_dir('uploads/x/'), 7200)
        self.assertEqual(s3_logs.prune_cache(), 2)
        self.assertEqual(os.listdir(self.cache), [])

    @override_settings(LOGS_S3_CACHE_TTL=60)
    def test_the_ttl_is_a_setting(self):
        with self.fake_generator():
            generate_item(self.storage, RUN, 'json/node_id5.json')
        self.age(self.run_dir(), 30)
        self.assertEqual(s3_logs.prune_cache(), 0)
        self.age(self.run_dir(), 90)
        self.assertEqual(s3_logs.prune_cache(), 1)

    def test_prune_keeps_a_folder_in_use(self):
        with self.fake_generator():
            generate_item(self.storage, RUN, 'json/node_id5.json')
        with s3_logs._run_lock(self.run_dir()):
            self.age(self.run_dir(), 7200)
            self.assertEqual(s3_logs.prune_cache(), 0)
        self.assertTrue(os.path.isdir(self.run_dir()))

    def test_a_pruned_page_is_generated_again(self):
        with self.fake_generator() as run:
            generate_item(self.storage, RUN, 'json/node_id5.json')
            self.age(self.run_dir(), 7200)
            s3_logs.prune_cache()
            path = generate_item(self.storage, RUN, 'json/node_id5.json')
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(self.download_file.call_count, 2)

    def test_requests_sweep_at_most_every_interval(self):
        with mock.patch.object(s3_logs, 'prune_cache') as prune:
            s3_logs.maybe_prune_cache().join()
            self.assertIsNone(s3_logs.maybe_prune_cache())
        prune.assert_called_once()

    def test_the_sweep_does_not_hold_up_the_request(self):
        release = threading.Event()
        with mock.patch.object(s3_logs, 'prune_cache', side_effect=lambda now: release.wait(5)):
            started = time.monotonic()
            thread = s3_logs.maybe_prune_cache()
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(thread.is_alive())
            release.set()
            thread.join()

    def test_a_failed_generation_leaves_no_partial_page(self):
        def run(command, **_kwargs):
            req_path = command[2].removeprefix('--req-path=')
            os.makedirs(os.path.dirname(req_path), exist_ok=True)
            with open(req_path, 'w') as f:
                f.write('{"truncated": ')
            return subprocess.CompletedProcess(command, 2, '', 'killed')

        page = os.path.join(self.run_dir(), 'json', 'node_id5.json')
        with mock.patch('subprocess.run', side_effect=run):  # noqa: SIM117
            with self.assertRaises(s3_logs.LogGenerationError):
                generate_item(self.storage, RUN, 'json/node_id5.json')
        self.assertFalse(os.path.exists(page))
        self.assertEqual([n for n in os.listdir(self.run_dir()) if n.startswith('.gen')], [])
        with self.fake_generator():
            self.assertEqual(generate_item(self.storage, RUN, 'json/node_id5.json'), page)

    def test_a_timed_out_generation_leaves_no_partial_directory(self):
        def run(command, **_kwargs):
            req_path = command[2].removeprefix('--req-path=')
            os.makedirs(req_path, exist_ok=True)
            with open(os.path.join(req_path, 'node_1_0.json'), 'w') as f:
                f.write('{"truncated": ')
            raise subprocess.TimeoutExpired(command, 1)

        with mock.patch('subprocess.run', side_effect=run):  # noqa: SIM117
            with self.assertRaises(s3_logs.LogGenerationError):
                generate_item(self.storage, RUN, 'json')
        self.assertFalse(os.path.exists(os.path.join(self.run_dir(), 'json')))
        self.assertFalse(os.path.exists(os.path.join(self.run_dir(), '.json-complete')))

    def test_a_path_above_the_runs_leaves_no_lock_behind(self):
        with self.fake_generator():
            generate_item(self.storage, RUN, 'json/node_id5.json')
        above = 'uploads/ts/'
        self.mocks[0].side_effect = lambda key, **_: key != f'{above}raw_log_bundle.tpxz'
        with self.fake_generator() as run:
            self.assertIsNone(generate_item(self.storage, above, 'json/x.json'))
        run.assert_not_called()
        self.assertFalse(os.path.exists(os.path.join(self.run_dir(above), '.lock')))
        # So the sweep cannot take the folder above for a run and delete the real ones.
        self.assertEqual(s3_logs.prune_cache(ttl=0), 1)
        self.assertEqual(s3_logs.prune_cache(ttl=0), 0)

    def test_a_directory_is_generated_whole_once(self):
        with self.fake_generator() as run:
            path = generate_item(self.storage, RUN, 'json')
            again = generate_item(self.storage, RUN, 'json')
        self.assertEqual(path, again)
        self.assertTrue(os.path.isfile(os.path.join(path, 'node_1_0.json')))
        self.assertEqual(run.call_count, 1)

    def test_the_command_prunes_with_a_given_ttl(self):
        with self.fake_generator():
            generate_item(self.storage, RUN, 'json/node_id5.json')
        out = io.StringIO()
        call_command('prune_logs_cache', '--ttl', '0', stdout=out)
        self.assertIn('Pruned 1 run folder(s)', out.getvalue())
        self.assertFalse(os.path.exists(self.run_dir()))

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
        self.assertFalse(os.path.exists(self.run_dir()))

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

    @mock.patch('bublik.core.log.s3_logs.maybe_prune_cache', new=mock.Mock())
    @mock.patch('bublik.core.s3.upload_fileobj')
    @mock.patch('bublik.core.s3.open_object', new=mock.Mock(return_value=None))
    def test_missing_pages_are_generated_and_not_stored(self, upload_fileobj):
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
        # S3 keeps only what was uploaded.
        upload_fileobj.assert_not_called()

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
            '/api/v2/logs/s3/bublik-logs/uploads/../elsewhere/x.json',
            '/api/v2/logs/s3/bublik-logs/uploads/./x.json',
            '/api/v2/logs/s3/bublik-logs/uploads//x.json',
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

    @mock.patch('bublik.core.log.s3_logs.maybe_prune_cache', new=mock.Mock())
    @mock.patch('bublik.core.s3.object_exists', new=mock.Mock(return_value=True))
    def test_a_bundle_runs_json_directory_is_generated_and_listed(self):
        generated = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, generated)
        os.mkdir(os.path.join(generated, 'images'))
        with open(os.path.join(generated, 'node_1_0.json'), 'w'):
            pass
        with mock.patch.object(s3_logs, 'generate_item', return_value=generated) as generate:
            response = self.client.get(f'/api/v2/logs/s3/bublik-logs/{RUN}json/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate.call_args.args[1:], (RUN, 'json'))
        body = response.content.decode()
        for name in ('images/', 'node_1_0.json'):
            self.assertIn(f'href="{name}"', body)

    @mock.patch('bublik.core.s3.object_exists', new=mock.Mock(return_value=False))
    @mock.patch(
        'bublik.core.s3.list_entries',
        new=mock.Mock(return_value=([], [f'{RUN}json/node_1_0.json'])),
    )
    def test_ready_made_pages_are_listed_from_s3(self):
        with mock.patch.object(s3_logs, 'generate_item') as generate:
            response = self.client.get(f'/api/v2/logs/s3/bublik-logs/{RUN}json/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="node_1_0.json"', response.content.decode())
        generate.assert_not_called()

    def test_writes_are_not_allowed(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)


@override_settings(SECURE_HTTP=True, URL_PREFIX='', ENABLE_JSON_LOGS_PROXY=True, BUBLIK_FQDN='')
@mock.patch.object(logs_storage, 'logs_bases', new=mock.Mock(return_value=[HTTP_BASE, S3_BASE]))
class JsonLogUrlsTest(SimpleTestCase):
    def urls(self, source, origin):
        result = mock.Mock(test_run=object(), exec_seqno=5, project_id=1)
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

    def test_the_html_log_url_is_absolute_for_the_requesting_client(self):
        result = mock.Mock(test_run=object(), exec_seqno=5, project_id=1)
        services = 'bublik.core.log.services'
        with mock.patch(f'{services}.ResultService.get_result', return_value=result):  # noqa: SIM117
            with mock.patch(
                f'{services}.get_result_log', return_value='s3://b/run/html/x.html'
            ):
                self.assertEqual(
                    LogService.get_html_log_url(1, 'https://bublik.example'),
                    'https://bublik.example/api/v2/logs/s3/b/run/html/x.html',
                )


@override_settings(
    SECURE_HTTP=True, URL_PREFIX='', S3_PRESIGN_EXPIRY=60, ENABLE_JSON_LOGS_PROXY=False
)
class PresignedLogUrlsTest(SimpleTestCase):
    public_base = MappingProxyType(
        {**S3_BASE, 'storage': {'type': 's3', 'public_endpoint_url': 'https://s3.example.com'}},
    )

    def urls(self, base, exists):
        result = mock.Mock(test_run=object(), exec_seqno=5, project_id=1)
        services = 'bublik.core.log.services'
        with mock.patch.object(logs_storage, 'logs_bases', return_value=[dict(base)]):  # noqa: SIM117
            with mock.patch('bublik.core.s3.object_exists', side_effect=exists):
                with mock.patch(f'{services}.ResultService.get_result', return_value=result):
                    with mock.patch(
                        f'{services}.get_sources', return_value=f's3://bublik-logs/{RUN}'
                    ):
                        return LogService.get_json_log_urls(1, None, 'bublik.example')

    def test_existing_pages_on_a_public_endpoint_are_presigned(self):
        urls = self.urls(self.public_base, lambda key, **_: key.endswith('.json'))
        run = RUN.replace(':', '%3A')
        self.assertTrue(
            urls['url'].startswith(f'https://s3.example.com/bublik-logs/{run}json/')
        )
        self.assertIn('X-Amz-Signature=', urls['url'])

    def test_missing_pages_go_through_bublik(self):
        urls = self.urls(self.public_base, lambda key, **_: False)
        self.assertTrue(urls['url'].startswith('https://bublik.example/api/v2/logs/s3/'))

    @override_settings(ENABLE_JSON_LOGS_PROXY=True)
    def test_the_proxy_switch_keeps_existing_pages_on_bublik(self):
        # A presigned S3 URL is cross-origin for the browser; the switch exists
        # for deployments that cannot allow that.
        with mock.patch('bublik.core.s3.public_read_url') as presign:
            urls = self.urls(self.public_base, lambda key, **_: True)
        self.assertTrue(urls['url'].startswith('https://bublik.example/api/v2/logs/s3/'))
        self.assertTrue(
            urls['attachments_url'].startswith('https://bublik.example/api/v2/logs/s3/')
        )
        presign.assert_not_called()

    def test_without_a_public_endpoint_everything_goes_through_bublik(self):
        urls = self.urls(S3_BASE, lambda key, **_: True)
        self.assertTrue(urls['url'].startswith('https://bublik.example/api/v2/logs/s3/'))
        self.assertTrue(
            urls['attachments_url'].startswith('https://bublik.example/api/v2/logs/s3/')
        )
