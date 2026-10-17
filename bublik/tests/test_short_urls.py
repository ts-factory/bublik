# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from bublik.data.models import EndpointURL


_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}


def short_url(path):
    prefix = f'/{settings.URL_PREFIX}' if settings.URL_PREFIX else ''
    return f'{prefix}/short/{path}'


@override_settings(CACHES={'default': _DUMMY, 'run': _DUMMY, 'project': _DUMMY})
class ShortURLRedirectTest(TestCase):
    def assert_redirects_to_endpoint(self, endpoint_hash):
        EndpointURL.objects.create(
            created=timezone.now(),
            view='log',
            endpoint='/123?focusId=456',
            hash=endpoint_hash,
        )

        response = self.client.get(short_url(f'log/{endpoint_hash}'))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response['Location'].endswith('/log/123?focusId=456'))

    def test_hash_with_underscore(self):
        self.assert_redirects_to_endpoint('4n6xo_knTPQ=')

    def test_hash_with_hyphen(self):
        self.assert_redirects_to_endpoint('4n6xo-knTPQ=')

    def test_hash_with_plus(self):
        self.assert_redirects_to_endpoint('4n6xo+knTPQ=')
