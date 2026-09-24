# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import socket
from unittest import mock

from django.db import IntegrityError
from django.test import override_settings
from django.urls import NoReverseMatch, reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from bublik.data.models import User, UserMcpServer
from bublik.tests.urlconf import ReloadUrlconfMixin, reload_urlconf


_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}

SECRET = 'Bearer super-secret-token-value'
PUBLIC_IP = '93.184.216.34'


def _policy(**user_mcp_servers):
    """Patch the active ``ai`` config with the given user-server policy."""
    raw = {
        'providers': [],
        'mcp_servers': [{'id': 'github', 'url': 'https://api.githubcopilot.com/mcp/'}],
        'user_mcp_servers': user_mcp_servers,
    }
    return mock.patch('bublik.ai.config.get_raw_ai_config', return_value=raw)


ALLOWED = {'allowed_hosts': ['mcp.example.com', '*.tools.example'], 'max_per_user': 2}


@override_settings(
    AI_CHAT_ENABLED=True,
    CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM},
)
class UserMcpServersApiTest(ReloadUrlconfMixin, APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.other = User.objects.create_user(email='b@example.com', password='pw12345!')

    def _auth(self, user):
        self.client.cookies['access_token'] = str(AccessToken.for_user(user))

    def _url(self, server_id=None):
        if server_id is None:
            return reverse('api-v2:chat-mcp-servers-list')
        return reverse('api-v2:chat-mcp-servers-detail', args=[server_id])

    def _create(self, user=None, **overrides):
        body = {
            'name': 'My Server',
            'url': 'https://mcp.example.com/mcp',
            'headers': {'Authorization': SECRET},
        }
        body.update(overrides)
        self._auth(user or self.user)
        return self.client.post(self._url(), body, format='json')

    def test_requires_authentication(self):
        self.assertEqual(self.client.get(self._url()).status_code, status.HTTP_403_FORBIDDEN)
        response = self.client.post(
            self._url(), {'name': 'x', 'url': 'https://a/'}, format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_route_absent_when_chat_disabled(self):
        with override_settings(AI_CHAT_ENABLED=False), reload_urlconf():
            self.assertRaises(NoReverseMatch, reverse, 'api-v2:chat-mcp-servers-list')
        # Re-import the URLconf under the class settings for later tests.
        with reload_urlconf():
            self.assertEqual(
                reverse('api-v2:chat-mcp-servers-list'), '/api/v2/chat/mcp-servers/'
            )

    def test_default_policy_refuses_every_host(self):
        with _policy():
            for url in ('https://mcp.example.com/mcp', f'http://{PUBLIC_IP}/mcp'):
                response = self._create(url=url)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, url)
                self.assertIn('allowed_hosts', str(response.data))
        self.assertEqual(UserMcpServer.objects.count(), 0)

    def test_features_flag_follows_policy(self):
        url = reverse('api-v2:server-features')
        with _policy():
            self.assertFalse(self.client.get(url).data['user_mcp_servers_enabled'])
        with _policy(allowed_hosts=['mcp.example.com']):
            self.assertTrue(self.client.get(url).data['user_mcp_servers_enabled'])
        with _policy(allow_any_public_host=True):
            self.assertTrue(self.client.get(url).data['user_mcp_servers_enabled'])

    def test_allow_any_public_host_blocks_private_unless_listed(self):
        with _policy(allow_any_public_host=True):
            self.assertEqual(
                self._create(url=f'https://{PUBLIC_IP}/mcp').status_code,
                status.HTTP_201_CREATED,
            )
            for url in ('http://localhost:8001/mcp', 'http://10.0.0.1/mcp', 'http://[::1]/mcp'):
                self.assertEqual(
                    self._create(name=url, url=url).status_code,
                    status.HTTP_400_BAD_REQUEST,
                    url,
                )
            with mock.patch.object(
                socket, 'getaddrinfo', return_value=[(0, 0, 0, '', ('172.18.0.3', 0))]
            ):
                response = self._create(name='redis', url='http://redis:6379/')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        with _policy(allow_any_public_host=True, allowed_hosts=['localhost']):
            response = self._create(name='local', url='http://localhost:8001/mcp')
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_stores_headers_encrypted_and_never_returns_them(self):
        with _policy(**ALLOWED):
            response = self._create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(response.data['name'], 'My Server')
        self.assertEqual(response.data['slug'], 'my_server')
        self.assertEqual(response.data['header_names'], ['Authorization'])
        self.assertTrue(response.data['enabled'])
        self.assertNotIn('headers', response.data)
        self.assertNotIn(SECRET.encode(), response.content)

        server = UserMcpServer.objects.get()
        self.assertTrue(server.headers_encrypted.startswith('gAAAA'))
        self.assertNotIn('super-secret', server.headers_encrypted)
        self.assertEqual(server.get_headers(), {'Authorization': SECRET})
        self.assertNotIn('super-secret', repr(server))

        listing = self.client.get(self._url())
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual([s['id'] for s in listing.data], [server.id])
        self.assertNotIn(SECRET.encode(), listing.content)

    def test_list_shows_only_own_servers(self):
        with _policy(**ALLOWED):
            self._create()
            self._create(user=self.other, name='Theirs')
        self._auth(self.user)
        self.assertEqual([s['name'] for s in self.client.get(self._url()).data], ['My Server'])

    def test_create_validation_errors(self):
        with _policy(**ALLOWED):
            for body in (
                {'url': 'ftp://mcp.example.com/'},
                {'url': 'https://elsewhere.example.com/mcp'},
                {'name': '***'},
                {'headers': {'Host': 'x'}},
                {'headers': {'Authorization': 'a\r\nX: y'}},
                {'headers': {'Bad Name': 'x'}},
                {'headers': 'not-a-mapping'},
            ):
                response = self._create(**body)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, body)
        self.assertEqual(UserMcpServer.objects.count(), 0)

    def test_slug_collisions(self):
        with _policy(**ALLOWED):
            self.assertEqual(self._create().status_code, status.HTTP_201_CREATED)
            # Same slug, different spelling.
            response = self._create(name='my-server')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn('name', response.data.get('messages', response.data))
            # Taken by the admin-configured server `github`.
            response = self._create(name='GitHub')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            # Another user may reuse the name.
            self.assertEqual(self._create(user=self.other).status_code, status.HTTP_201_CREATED)

    def test_a_rename_race_is_a_validation_error(self):
        with _policy(**ALLOWED):
            server_id = self._create().data['id']
            with mock.patch.object(UserMcpServer, 'save', side_effect=IntegrityError):
                response = self.client.patch(
                    self._url(server_id), {'name': 'Other'}, format='json'
                )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('already have a server', str(response.data))

    def test_per_user_limit(self):
        with _policy(**ALLOWED):
            self.assertEqual(self._create(name='one').status_code, status.HTTP_201_CREATED)
            self.assertEqual(self._create(name='two').status_code, status.HTTP_201_CREATED)
            response = self._create(name='three')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('limit', str(response.data))

    def test_patch_merges_headers_and_rechecks_url(self):
        with _policy(**ALLOWED):
            server_id = self._create(headers={'Authorization': SECRET, 'X-Keep': 'k'}).data[
                'id'
            ]
            response = self.client.patch(
                self._url(server_id),
                {
                    'name': 'Renamed',
                    'enabled': False,
                    'headers': {
                        'Authorization': 'Bearer rotated',
                        'X-Keep': None,
                        'X-New': 'n',
                    },
                },
                format='json',
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            self.assertEqual(response.data['name'], 'Renamed')
            self.assertEqual(response.data['slug'], 'renamed')
            self.assertFalse(response.data['enabled'])
            self.assertEqual(response.data['header_names'], ['Authorization', 'X-New'])
            self.assertNotIn(b'rotated', response.content)
            server = UserMcpServer.objects.get(pk=server_id)
            self.assertEqual(
                server.get_headers(), {'Authorization': 'Bearer rotated', 'X-New': 'n'}
            )

            # Omitting headers keeps them; the URL is re-checked against the policy.
            response = self.client.patch(
                self._url(server_id), {'url': 'https://elsewhere.example.com/'}, format='json'
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            response = self.client.patch(
                self._url(server_id), {'url': 'https://a.tools.example/mcp'}, format='json'
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
            server.refresh_from_db()
            self.assertEqual(server.url, 'https://a.tools.example/mcp')
            self.assertEqual(
                server.get_headers(), {'Authorization': 'Bearer rotated', 'X-New': 'n'}
            )

    def test_patch_and_delete_of_someone_elses_server_are_not_found(self):
        with _policy(**ALLOWED):
            server_id = self._create(user=self.other).data['id']
            self._auth(self.user)
            response = self.client.patch(self._url(server_id), {'name': 'x'}, format='json')
            self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
            self.assertEqual(
                self.client.delete(self._url(server_id)).status_code, status.HTTP_404_NOT_FOUND
            )
            self.assertEqual(
                self.client.delete(self._url(999999)).status_code, status.HTTP_404_NOT_FOUND
            )
        self.assertTrue(UserMcpServer.objects.filter(pk=server_id).exists())

    def test_delete(self):
        with _policy(**ALLOWED):
            server_id = self._create().data['id']
        response = self.client.delete(self._url(server_id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(UserMcpServer.objects.filter(pk=server_id).exists())

    def test_undecryptable_headers_are_listed_as_none(self):
        with _policy(**ALLOWED):
            server_id = self._create().data['id']
        UserMcpServer.objects.filter(pk=server_id).update(headers_encrypted='gAAAAgarbage')
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['header_names'], [])
