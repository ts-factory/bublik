# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

import uuid

from django.test.utils import override_settings
from django.urls import reverse
import fakeredis
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from bublik.ai import run_store
from bublik.data.models import ChatThread, User


_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
# Keep `default` as DummyCache (as in production) so the per-site cache
# middleware never caches/repays responses across tests; only the redis-backed
# project/run caches need a local stand-in.
_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class ChatThreadsApiTest(APITestCase):
    def setUp(self):
        run_store._sredis = fakeredis.FakeRedis(decode_responses=True)
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.other = User.objects.create_user(email='b@example.com', password='pw12345!')

    def tearDown(self):
        run_store._sredis = None

    def _auth(self, user):
        self.client.cookies['access_token'] = str(AccessToken.for_user(user))

    def _url(self, thread_id=None):
        if thread_id is None:
            return reverse('api-v2:chat-threads-list')
        return reverse('api-v2:chat-threads-detail', args=[thread_id])

    def test_retrieve_returns_server_owned_messages(self):
        self._auth(self.user)
        tid = str(uuid.uuid4())
        messages = [
            {'role': 'assistant', 'parts': [{'type': 'text', 'content': 'hi'}]},
            {'role': 'user', 'parts': [{'type': 'text', 'content': 'List runs today'}]},
        ]
        ChatThread.objects.create(
            id=tid,
            user=self.user,
            title='List runs today',
            messages=messages,
        )

        resp = self.client.get(self._url(tid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data['messages']), 2)

    def test_list_excludes_messages_and_archived(self):
        self._auth(self.user)
        tid = str(uuid.uuid4())
        ChatThread.objects.create(
            id=tid,
            user=self.user,
            title='hey',
            messages=[{'role': 'user', 'parts': [{'type': 'text', 'content': 'hey'}]}],
        )
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertNotIn('messages', resp.data[0])

        # Archive -> hidden by default, visible with ?archived=true.
        self.client.patch(self._url(tid), {'is_archived': True}, format='json')
        self.assertEqual(len(self.client.get(self._url()).data), 0)
        self.assertEqual(len(self.client.get(self._url() + '?archived=true').data), 1)

    def test_rename(self):
        self._auth(self.user)
        tid = str(uuid.uuid4())
        ChatThread.objects.create(id=tid, user=self.user, title='Original')
        resp = self.client.patch(self._url(tid), {'title': 'Renamed'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(ChatThread.objects.get(id=tid).title, 'Renamed')

    def test_patch_cannot_replace_server_owned_messages(self):
        self._auth(self.user)
        tid = str(uuid.uuid4())
        original = [{'role': 'assistant', 'parts': [{'type': 'text', 'content': 'trusted'}]}]
        thread = ChatThread.objects.create(id=tid, user=self.user, messages=original)

        resp = self.client.patch(
            self._url(tid),
            {'messages': [{'role': 'user', 'parts': [{'type': 'text', 'content': 'fake'}]}]},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        thread.refresh_from_db()
        self.assertEqual(thread.messages, original)

    def test_delete(self):
        self._auth(self.user)
        tid = str(uuid.uuid4())
        ChatThread.objects.create(id=tid, user=self.user)
        resp = self.client.delete(self._url(tid))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ChatThread.objects.filter(id=tid).exists())

    def test_user_isolation(self):
        # User A creates a thread.
        self._auth(self.user)
        tid = str(uuid.uuid4())
        ChatThread.objects.create(id=tid, user=self.user)

        # User B cannot see, read, modify, or delete it.
        self._auth(self.other)
        self.assertEqual(len(self.client.get(self._url()).data), 0)
        self.assertEqual(
            self.client.get(self._url(tid)).status_code,
            status.HTTP_404_NOT_FOUND,
        )
        self.assertEqual(self.client.patch(self._url(tid), {'title': 'nope'}).status_code, 404)
        self.assertEqual(
            self.client.delete(self._url(tid)).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_requires_auth(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_malformed_thread_id_is_not_found(self):
        self._auth(self.user)
        resp = self.client.get(self._url('not-a-uuid'))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_is_not_available_to_clients(self):
        self._auth(self.user)
        tid = str(uuid.uuid4())
        thread = ChatThread.objects.create(
            id=tid,
            user=self.user,
            title='Original',
            messages=[],
            context_state={'context_tokens': 5000, 'summary': 's'},
        )
        resp = self.client.put(
            self._url(tid),
            {'messages': [{'role': 'user', 'parts': [{'type': 'text', 'content': 'hi'}]}]},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        thread.refresh_from_db()
        self.assertEqual(thread.context_state, {'context_tokens': 5000, 'summary': 's'})
