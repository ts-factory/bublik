# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import os
import shutil
import tempfile
from unittest import mock

from django.test import SimpleTestCase
import requests

from bublik.core.exceptions import ImportrunsError
from bublik.core.importruns.source.run_traversal import HTTPDirectoryTraverser, traverser_for
from bublik.core.importruns.source.storage import HTTPRunSource, run_source_for


def _response(status_code, content=b''):
    response = mock.Mock(status_code=status_code)
    response.iter_content.return_value = [content]
    if status_code >= 400:  # noqa: PLR2004
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(str(status_code))
    return response


class HTTPRunSourceTest(SimpleTestCase):
    url = 'https://logs.example/logs/ts/2026/09/23/cfg-10:00:00/'

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.source = HTTPRunSource(self.url)
        # Earlier import tests can leave a Celery task context behind, which
        # would send the fetch logger looking for a per-task log file.
        patcher = mock.patch('bublik.core.url.get_task_or_server_logger')
        patcher.start()
        self.addCleanup(patcher.stop)

    @mock.patch('bublik.core.url.requests.get')
    def test_fetch_saves_the_file(self, get):
        get.return_value = _response(200, b'{"version": 1}')
        self.assertTrue(self.source.fetch_to_dir('meta_data.json', self.dir))
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], self.url + 'meta_data.json')
        with open(os.path.join(self.dir, 'meta_data.json'), 'rb') as f:
            self.assertEqual(f.read(), b'{"version": 1}')

    @mock.patch('bublik.core.url.requests.get')
    def test_fetch_miss_leaves_no_file(self, get):
        get.return_value = _response(404)
        self.assertFalse(self.source.fetch_to_dir('bublik.json', self.dir))
        self.assertEqual(os.listdir(self.dir), [])

    @mock.patch('bublik.core.url.requests.get')
    def test_exists(self, get):
        get.side_effect = [_response(200), _response(404)]
        self.assertTrue(self.source.exists('.done'))
        self.assertFalse(self.source.exists('missing'))

    def test_location(self):
        self.assertEqual(self.source.location('bublik.json'), self.url + 'bublik.json')


class SchemeSelectionTest(SimpleTestCase):
    def test_http_urls_use_http(self):
        for url in ('http://a/logs/', 'HTTPS://a/logs/'):
            with self.subTest(url=url):
                self.assertIsInstance(run_source_for(url), HTTPRunSource)

    @mock.patch('bublik.core.importruns.source.run_traversal.get_import_job_task')
    def test_http_urls_use_the_directory_traverser(self, get_import_job_task):
        self.assertIsInstance(traverser_for('http://a/logs/', 1), HTTPDirectoryTraverser)
        get_import_job_task.assert_called_once_with(1)

    def test_unknown_scheme_is_a_clear_import_error(self):
        for select in (run_source_for, lambda url: traverser_for(url, 1)):
            with self.subTest(select=select), self.assertRaises(ImportrunsError) as caught:
                select('ftp://a/logs/')
            self.assertIn('ftp', caught.exception.message)
