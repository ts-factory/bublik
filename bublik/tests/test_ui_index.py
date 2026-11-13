# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from unittest import mock

from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import reverse


_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class UiIndexTest(TestCase):
    def get(self, url):
        with mock.patch(
            'bublik.interfaces.api_v2.index.render',
            return_value=HttpResponse('<html></html>'),
        ):
            return self.client.get(url)

    def test_index_is_revalidated(self):
        response = self.get(reverse('ui-v2-index'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')

    def test_nested_route_is_revalidated(self):
        response = self.get(reverse('ui-v2-index') + 'runs/12')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-cache')
