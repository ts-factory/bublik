# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import io
from unittest import mock

from botocore.stub import ANY, Stubber
from django.test import SimpleTestCase, override_settings

from bublik.core import s3


_TARGET = s3.S3Target(
    bucket='logs',
    endpoint_url='http://127.0.0.1:8333',
    access_key='key',
    secret_key='secret',
)


class S3TargetTest(SimpleTestCase):
    @override_settings(
        S3_BUCKET='chat',
        S3_ENDPOINT_URL='http://s3.local',
        S3_ACCESS_KEY='a',
        S3_SECRET_KEY='b',
        S3_REGION='eu-west-1',
        S3_PUBLIC_ENDPOINT_URL='',
    )
    def test_default_target_comes_from_settings(self):
        target = s3.default_target()
        self.assertEqual(target.bucket, 'chat')
        self.assertEqual(target.endpoint_url, 'http://s3.local')
        self.assertEqual(target.region, 'eu-west-1')
        self.assertEqual(s3.default_target('logs').bucket, 'logs')

    def test_repr_hides_credentials(self):
        self.assertNotIn('secret', repr(_TARGET))

    def test_clients_are_cached_per_connection(self):
        other = s3.S3Target(
            bucket='other',
            endpoint_url=_TARGET.endpoint_url,
            access_key='key2',
            secret_key='secret',
        )
        same_connection = s3.S3Target(
            bucket='another-bucket',
            endpoint_url=_TARGET.endpoint_url,
            access_key='key',
            secret_key='secret',
        )
        self.assertIsNot(s3.client_for(_TARGET), s3.client_for(other))
        self.assertIs(s3.client_for(_TARGET), s3.client_for(same_connection))


class S3HelpersTest(SimpleTestCase):
    def setUp(self):
        self.client = s3.client_for(_TARGET)
        self.stubber = Stubber(self.client)
        self.stubber.activate()
        self.addCleanup(self.stubber.deactivate)
        s3._buckets_ready.add((_TARGET.endpoint_url, _TARGET.bucket))
        self.addCleanup(s3._buckets_ready.discard, (_TARGET.endpoint_url, _TARGET.bucket))

    def test_object_exists(self):
        self.stubber.add_response('head_object', {}, {'Bucket': 'logs', 'Key': 'a'})
        self.stubber.add_client_error(
            'head_object', service_error_code='404', http_status_code=404
        )
        self.assertTrue(s3.object_exists('a', target=_TARGET))
        self.assertFalse(s3.object_exists('b', target=_TARGET))

    def test_open_missing_object_returns_none(self):
        self.stubber.add_client_error(
            'get_object', service_error_code='NoSuchKey', http_status_code=404
        )
        self.assertIsNone(s3.open_object('missing', target=_TARGET))

    def test_other_errors_propagate(self):
        self.stubber.add_client_error(
            'head_object', service_error_code='403', http_status_code=403
        )
        with self.assertRaises(s3.ClientError):
            s3.object_exists('a', target=_TARGET)

    def test_list_prefixes(self):
        self.stubber.add_response(
            'list_objects_v2',
            {'CommonPrefixes': [{'Prefix': 'up/a/'}, {'Prefix': 'up/b/'}]},
            {'Bucket': 'logs', 'Prefix': 'up/', 'Delimiter': '/'},
        )
        self.assertEqual(s3.list_prefixes('up/', target=_TARGET), ['up/a/', 'up/b/'])

    def test_upload_bytes_uses_the_target_bucket(self):
        self.stubber.add_response(
            'put_object',
            {},
            {'Bucket': 'logs', 'Key': 'k', 'Body': b'x', 'ContentType': 'text/plain'},
        )
        s3.upload_bytes('k', b'x', 'text/plain', target=_TARGET)
        self.stubber.assert_no_pending_responses()

    def test_upload_fileobj_streams_to_the_target_bucket(self):
        self.stubber.add_response(
            'put_object',
            {},
            {
                'Bucket': 'logs',
                'Key': 'k',
                'Body': ANY,
                'ContentType': 'application/json',
                'ChecksumAlgorithm': ANY,
            },
        )
        s3.upload_fileobj('k', io.BytesIO(b'{}'), 'application/json', target=_TARGET)
        self.stubber.assert_no_pending_responses()


class PresignTest(SimpleTestCase):
    def test_no_public_endpoint_means_no_url(self):
        self.assertIsNone(s3.public_read_url('k', target=_TARGET))

    @override_settings(S3_PRESIGN_EXPIRY=60)
    def test_read_url_is_inline_and_on_the_public_host(self):
        target = s3.S3Target(
            bucket='logs',
            endpoint_url='http://127.0.0.1:8333',
            access_key='key',
            secret_key='secret',
            public_endpoint_url='https://s3.example.com',
        )
        url = s3.public_read_url('run/json/node_1_0.json', target=target)
        self.assertTrue(url.startswith('https://s3.example.com/logs/run/json/node_1_0.json?'))
        self.assertNotIn('attachment', url)

    def test_chat_download_url_keeps_its_default_target(self):
        with mock.patch.object(s3, 'default_target', return_value=_TARGET):
            self.assertIsNone(s3.public_download_url('k', 'f.txt'))
