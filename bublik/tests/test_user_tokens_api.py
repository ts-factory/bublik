# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from bublik.core.user_token import UserTokenService
from bublik.core.user_token.services import (
    MAX_TOKENS_CREATED_PER_HOUR,
    MAX_TOKENS_PER_USER,
)
from bublik.data.models import (
    TOKEN_PREFIX,
    TokenStatus,
    User,
    UserRoles,
    UserToken,
)
from bublik.interfaces.api_v2.user_token.serializers import IssuedUserTokenSerializer


RATE_LIMIT = 'bublik.core.user_token.services.MAX_TOKENS_CREATED_PER_HOUR'

_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}
_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class UserTokensApiTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='a@example.com', password='pw12345!')
        self.other = User.objects.create_user(email='b@example.com', password='pw12345!')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )

    def _cookie_auth(self, user):
        self.client.cookies['access_token'] = str(AccessToken.for_user(user))

    def _clear_cookie(self):
        self.client.cookies.pop('access_token', None)

    def _bearer(self, raw_token):
        return {'HTTP_AUTHORIZATION': f'Bearer {raw_token}'}

    def _list_url(self):
        return reverse('api-v2:auth-tokens-list')

    def _revoke_url(self, token_id):
        return reverse('api-v2:auth-tokens-revoke', args=[token_id])

    def _all_url(self):
        return reverse('api-v2:auth-tokens-all')

    def _create(self, name='laptop', expires_in=30):
        self._cookie_auth(self.user)
        response = self.client.post(
            self._list_url(),
            {'name': name, 'expires_in': expires_in},
            format='json',
        )
        assert response.status_code == status.HTTP_201_CREATED, response.data
        self._clear_cookie()
        return response.data

    def test_create_returns_value_once_for_each_expiry_option(self):
        for index, expires_in in enumerate((7, 30, 90, None)):
            data = self._create(name=f'token-{index}', expires_in=expires_in)
            assert data['token'].startswith(TOKEN_PREFIX)
            assert data['prefix'] == data['token'][: len(data['prefix'])]
            if expires_in is None:
                assert data['expires_at'] is None
            else:
                assert data['expires_at'] is not None

    def test_list_never_returns_the_value(self):
        created = self._create()
        self._cookie_auth(self.user)
        response = self.client.get(self._list_url())
        assert response.status_code == status.HTTP_200_OK
        (listed,) = response.data
        assert 'token' not in listed
        assert 'token_hash' not in listed
        assert created['token'] not in str(response.content)
        assert listed['prefix'] == created['prefix']

    def test_no_recoverable_copy_is_stored(self):
        created = self._create()
        token = UserToken.objects.get(pk=created['id'])
        assert created['token'] not in str(token.__dict__)
        assert repr(token).find(created['token']) == -1

    def test_rejects_an_unsupported_expiry(self):
        self._cookie_auth(self.user)
        response = self.client.post(
            self._list_url(),
            {'name': 'nope', 'expires_in': 5},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_reusing_an_expired_tokens_name(self):
        expired = self._create(name='laptop', expires_in=7)
        UserToken.objects.filter(pk=expired['id']).update(
            expires_at=timezone.now() - timedelta(days=1),
        )
        self._cookie_auth(self.user)
        response = self.client.post(
            self._list_url(),
            {'name': 'laptop', 'expires_in': 30},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Revoke it first' in str(response.data)

    def test_a_token_cannot_issue_tokens(self):
        created = self._create()
        response = self.client.post(
            self._list_url(),
            {'name': 'minted', 'expires_in': None},
            format='json',
            **self._bearer(created['token']),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert not UserToken.objects.filter(name='minted').exists()

    def test_the_schema_names_the_value_field_as_sent(self):
        assert 'token' in IssuedUserTokenSerializer().fields
        assert 'value' not in IssuedUserTokenSerializer().fields

    def test_rejects_a_duplicate_active_name(self):
        self._create(name='laptop')
        self._cookie_auth(self.user)
        response = self.client.post(
            self._list_url(),
            {'name': 'laptop', 'expires_in': 30},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_token_authenticates_as_its_owner(self):
        created = self._create()
        response = self.client.get(
            reverse('api-v2:profile-info'),
            **self._bearer(created['token']),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['email'] == self.user.email

    def test_cookie_login_is_unchanged(self):
        self._cookie_auth(self.user)
        response = self.client.get(reverse('api-v2:profile-info'))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['email'] == self.user.email

    def test_failures_are_distinguishable(self):
        cases = {}

        cases['Invalid access token'] = TOKEN_PREFIX + 'not-a-real-token'

        expired = self._create(name='expired', expires_in=7)
        UserToken.objects.filter(pk=expired['id']).update(
            expires_at=timezone.now() - timedelta(days=1),
        )
        cases['Access token expired'] = expired['token']

        revoked = self._create(name='revoked')
        UserToken.objects.get(pk=revoked['id']).revoke(revoked_by=self.user)
        cases['Access token revoked'] = revoked['token']

        inactive_user = User.objects.create_user(email='c@example.com', password='pw12345!')
        inactive_raw = UserTokenService.issue(inactive_user, 'gone').value
        inactive_user.is_active = False
        inactive_user.save()
        cases['User account is deactivated'] = inactive_raw

        for expected, raw_token in cases.items():
            response = self.client.get(
                reverse('api-v2:profile-info'),
                **self._bearer(raw_token),
            )
            assert response.status_code == status.HTTP_403_FORBIDDEN, expected
            assert expected in str(response.data), (expected, response.data)

    def test_a_bad_token_does_not_fall_back_to_the_cookie(self):
        self._cookie_auth(self.user)
        response = self.client.get(
            reverse('api-v2:profile-info'),
            **self._bearer(TOKEN_PREFIX + 'bogus'),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'Invalid access token' in str(response.data)

    def test_a_bad_token_on_an_anonymous_endpoint_is_403(self):
        response = self.client.get(
            reverse('api-v2:config-list'),
            **self._bearer(TOKEN_PREFIX + 'bogus'),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert 'Invalid access token' in str(response.data)

    def test_deactivating_a_user_stops_their_tokens(self):
        created = self._create()
        self.user.is_active = False
        self.user.save()
        response = self.client.get(
            reverse('api-v2:profile-info'),
            **self._bearer(created['token']),
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_deactivating_a_user_stops_their_cookie_session(self):
        self._cookie_auth(self.user)
        self.user.is_active = False
        self.user.save()
        response = self.client.get(reverse('api-v2:profile-info'))
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_revoking_works_on_the_very_next_request(self):
        created = self._create()
        profile = reverse('api-v2:profile-info')
        alive = self.client.get(profile, **self._bearer(created['token']))
        assert alive.status_code == status.HTTP_200_OK

        self._cookie_auth(self.user)
        response = self.client.post(self._revoke_url(created['id']))
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == TokenStatus.REVOKED
        self._clear_cookie()

        dead = self.client.get(profile, **self._bearer(created['token']))
        assert dead.status_code == status.HTTP_403_FORBIDDEN

    def test_a_revoked_token_stays_listed_with_its_revoker(self):
        created = self._create()
        self._cookie_auth(self.user)
        self.client.post(self._revoke_url(created['id']))
        response = self.client.get(self._list_url())
        (listed,) = response.data
        assert listed['status'] == TokenStatus.REVOKED
        assert listed['revoked_by'] == self.user.email
        assert listed['revoked_at'] is not None

    def test_a_never_expiring_token_keeps_working(self):
        created = self._create(name='forever', expires_in=None)
        token = UserToken.objects.get(pk=created['id'])
        assert token.expires_at is None
        assert token.status == TokenStatus.ACTIVE

    def test_password_reset_does_not_revoke_tokens(self):
        created = self._create()
        self._cookie_auth(self.user)
        response = self.client.post(
            reverse('api-v2:profile-password-reset'),
            {
                'current_password': 'pw12345!',
                'new_password': 'pw54321!',
                'new_password_confirm': 'pw54321!',
            },
            format='json',
        )
        assert response.status_code == status.HTTP_200_OK
        self._clear_cookie()
        assert (
            self.client.get(
                reverse('api-v2:profile-info'),
                **self._bearer(created['token']),
            ).status_code
            == status.HTTP_200_OK
        )

    def test_last_used_is_recorded_and_not_rewritten_within_the_minute(self):
        created = self._create()
        token = UserToken.objects.get(pk=created['id'])
        assert token.last_used_at is None

        self.client.get(reverse('api-v2:profile-info'), **self._bearer(created['token']))
        token.refresh_from_db()
        first = token.last_used_at
        assert first is not None

        self.client.get(reverse('api-v2:profile-info'), **self._bearer(created['token']))
        token.refresh_from_db()
        assert token.last_used_at == first

    def test_active_token_cap(self):
        with mock.patch(RATE_LIMIT, MAX_TOKENS_PER_USER + 1):
            for index in range(MAX_TOKENS_PER_USER):
                UserTokenService.issue(self.user, f'token-{index}')
        self._cookie_auth(self.user)
        response = self.client.post(
            self._list_url(),
            {'name': 'one-too-many', 'expires_in': None},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert str(MAX_TOKENS_PER_USER) in str(response.data)

    def test_revoked_tokens_do_not_count_towards_the_cap(self):
        with mock.patch(RATE_LIMIT, MAX_TOKENS_PER_USER + 2):
            for index in range(MAX_TOKENS_PER_USER):
                issued = UserTokenService.issue(self.user, f'token-{index}')
            UserTokenService.revoke(issued.token.id, acting_user=self.user)

            self._cookie_auth(self.user)
            response = self.client.post(
                self._list_url(),
                {'name': 'replacement', 'expires_in': None},
                format='json',
            )
        assert response.status_code == status.HTTP_201_CREATED

    def test_creation_rate_limit(self):
        self._cookie_auth(self.user)
        for index in range(MAX_TOKENS_CREATED_PER_HOUR):
            created = self.client.post(
                self._list_url(),
                {'name': f'burst-{index}', 'expires_in': None},
                format='json',
            )
            assert created.status_code == status.HTTP_201_CREATED
        response = self.client.post(
            self._list_url(),
            {'name': 'over-the-limit', 'expires_in': None},
            format='json',
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'Too many tokens' in str(response.data)

    def test_a_user_only_sees_their_own_tokens(self):
        self._create(name='mine')
        UserTokenService.issue(self.other, 'theirs')
        self._cookie_auth(self.user)
        response = self.client.get(self._list_url())
        assert [row['name'] for row in response.data] == ['mine']

    def test_a_non_admin_cannot_revoke_someone_elses_token(self):
        issued = UserTokenService.issue(self.other, 'theirs')
        self._cookie_auth(self.user)
        response = self.client.post(self._revoke_url(issued.token.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert UserToken.objects.get(pk=issued.token.id).revoked_at is None

    def test_a_non_admin_cannot_list_every_token(self):
        self._cookie_auth(self.user)
        response = self.client.get(self._all_url())
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_admin_lists_and_revokes_any_token(self):
        issued = UserTokenService.issue(self.other, 'theirs')
        self._cookie_auth(self.admin)

        listed = self.client.get(self._all_url())
        assert listed.status_code == status.HTTP_200_OK
        owners = {row['owner'] for row in listed.data}
        assert self.other.email in owners
        assert all('token' not in row for row in listed.data)

        revoked = self.client.post(self._revoke_url(issued.token.id))
        assert revoked.status_code == status.HTTP_200_OK
        assert revoked.data['revoked_by'] == self.admin.email

    def test_the_command_issues_a_usable_token(self):
        out = StringIO()
        call_command(
            'issue_user_token',
            self.user.email,
            '--name',
            'from-cli',
            '--expires',
            'never',
            stdout=out,
        )
        printed = out.getvalue()
        raw_token = next(word for word in printed.split() if word.startswith(TOKEN_PREFIX))
        response = self.client.get(
            reverse('api-v2:profile-info'),
            **self._bearer(raw_token),
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.data['email'] == self.user.email
        assert UserToken.objects.get(name='from-cli').expires_at is None

    def test_the_command_refuses_a_deactivated_user(self):
        self.user.is_active = False
        self.user.save()
        with self.assertRaises(CommandError):
            call_command('issue_user_token', self.user.email, '--name', 'nope')

    def test_the_command_rejects_a_too_long_name(self):
        with self.assertRaises(CommandError):
            call_command('issue_user_token', self.user.email, '--name', 'x' * 65)

    def test_the_command_rejects_an_expired_tokens_name(self):
        issued = UserTokenService.issue(self.user, 'cli', expires_in=7)
        UserToken.objects.filter(pk=issued.token.id).update(
            expires_at=timezone.now() - timedelta(days=1),
        )
        with self.assertRaises(CommandError):
            call_command('issue_user_token', self.user.email, '--name', 'cli')

    def test_the_value_never_reaches_the_logs(self):
        created = self._create()
        with self.assertLogs(level='DEBUG') as captured:
            self.client.get(reverse('api-v2:profile-info'), **self._bearer(created['token']))
            self.client.get(
                reverse('api-v2:profile-info'),
                **self._bearer(TOKEN_PREFIX + 'bogus'),
            )
        assert all(created['token'] not in line for line in captured.output)
