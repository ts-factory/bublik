# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class ServerFeaturesApiTest(APITestCase):
    @override_settings(CHAT_ENABLED=False)
    def test_reports_disabled_chat(self):
        response = self.client.get(reverse('api-v2:server-features'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['chat_enabled'])

    @override_settings(CHAT_ENABLED=True)
    def test_reports_enabled_chat(self):
        response = self.client.get(reverse('api-v2:server-features'))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['chat_enabled'])
