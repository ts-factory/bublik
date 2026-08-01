# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Unit tests for the pure document rendering in :mod:`bublik.ai.rendering`."""

import csv
import io
import json

from django.test import SimpleTestCase
from pydantic_ai import ModelRetry

from bublik.ai.rendering import (
    CONTENT_TYPES,
    FileFormat,
    _reject_remote,
    render,
    sanitize_filename,
)


class SanitizeFilenameTest(SimpleTestCase):
    def test_strips_directory_components(self):
        self.assertEqual(sanitize_filename('../../etc/passwd', 'txt'), 'passwd.txt')

    def test_strips_backslash_directory_components(self):
        self.assertEqual(sanitize_filename(r'C:\evil\report', 'pdf'), 'report.pdf')

    def test_replaces_special_characters(self):
        self.assertEqual(sanitize_filename('a b*c?.md', 'md'), 'a b_c_.md')

    def test_normalizes_extension_to_format(self):
        self.assertEqual(sanitize_filename('data.csv', 'xlsx'), 'data.xlsx')

    def test_empty_stem_falls_back_to_generated(self):
        self.assertEqual(sanitize_filename('...', 'json'), 'generated.json')

    def test_caps_length_at_255(self):
        # The 255 cap is applied after the extension is appended, so an
        # extreme stem is truncated wholesale (extension included).
        out = sanitize_filename('x' * 500, 'txt')
        self.assertLessEqual(len(out), 255)


class RenderContentFormatsTest(SimpleTestCase):
    def test_txt_encodes_raw_content(self):
        self.assertEqual(render('txt', 'hello', None, 'title'), b'hello')

    def test_json_is_normalized_when_valid(self):
        out = render('json', '{"b":1,"a":2}', None, 'title')
        self.assertEqual(json.loads(out), {'b': 1, 'a': 2})
        # Pretty-printed (indented) output.
        self.assertIn(b'\n', out)

    def test_json_kept_raw_when_invalid(self):
        self.assertEqual(render('json', 'not json', None, 'title'), b'not json')

    def test_html_encodes_raw_content(self):
        self.assertEqual(render('html', '<p>x</p>', None, 't'), b'<p>x</p>')

    def test_missing_content_raises_model_retry(self):
        with self.assertRaises(ModelRetry):
            render('md', None, None, 'title')


class RenderTabularFormatsTest(SimpleTestCase):
    def test_csv_from_rows(self):
        out = render('csv', None, [['h1', 'h2'], ['a', 'b']], 'title')
        parsed = list(csv.reader(io.StringIO(out.decode('utf-8'))))
        self.assertEqual(parsed, [['h1', 'h2'], ['a', 'b']])

    def test_csv_accepts_raw_content(self):
        self.assertEqual(render('csv', 'a,b\n1,2', None, 'title'), b'a,b\n1,2')

    def test_xlsx_has_workbook_magic_bytes(self):
        out = render('xlsx', None, [['h'], ['v']], 'sheet')
        # xlsx is a zip container.
        self.assertTrue(out.startswith(b'PK'))

    def test_missing_rows_raises_model_retry(self):
        with self.assertRaises(ModelRetry):
            render('xlsx', None, None, 'title')


class RejectRemoteTest(SimpleTestCase):
    def test_rejects_http_url(self):
        with self.assertRaises(ValueError):
            _reject_remote('https://evil.example/x.png')

    def test_rejects_file_url(self):
        with self.assertRaises(ValueError):
            _reject_remote('file:///etc/passwd')


class ContentTypesTest(SimpleTestCase):
    def test_every_file_format_has_a_content_type(self):
        formats = set(FileFormat.__args__)
        self.assertEqual(formats, set(CONTENT_TYPES))
