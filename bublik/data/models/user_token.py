# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Personal access tokens for the REST API and the MCP server.
"""

from datetime import timedelta
import hashlib
import secrets
from typing import ClassVar

from django.db import models
from django.utils import timezone

from bublik.data.models.user import User


__all__ = [
    'TOKEN_PREFIX',
    'TokenErrorReason',
    'TokenStatus',
    'UserToken',
    'UserTokenError',
    'UserTokenManager',
]


# Makes tokens recognisable in logs and to secret scanners.
TOKEN_PREFIX = 'bpat_'

# Characters of the secret kept in the visible prefix.
PREFIX_SAMPLE_LEN = 6

# `last_used_at` is refreshed at most this often.
LAST_USED_RESOLUTION = timedelta(seconds=60)


class TokenStatus(models.TextChoices):
    ACTIVE = 'active'
    EXPIRED = 'expired'
    REVOKED = 'revoked'


class TokenErrorReason(models.TextChoices):
    """Why a presented token was refused."""

    INVALID = 'invalid', 'Invalid access token'
    EXPIRED = 'expired', 'Access token expired'
    REVOKED = 'revoked', 'Access token revoked'
    INACTIVE = 'inactive', 'User account is deactivated'


class UserTokenError(Exception):
    """A token was presented but cannot be used."""

    def __init__(self, reason: TokenErrorReason):
        self.reason = reason
        self.message = reason.label
        super().__init__(self.message)


def hash_token(raw_token: str) -> str:
    """Hash a token for storage and lookup.

    Plain SHA-256 is enough: the token is 256 random bits, not a password.
    """
    return hashlib.sha256(raw_token.encode()).hexdigest()


class UserTokenManager(models.Manager):
    def issue(self, user, name, expires_in_days=None):
        """Create a token and return ``(instance, raw_value)``. The value is not stored."""
        raw_token = TOKEN_PREFIX + secrets.token_urlsafe(32)
        expires_at = None
        if expires_in_days is not None:
            expires_at = timezone.now() + timedelta(days=expires_in_days)

        token = self.create(
            user=user,
            name=name,
            prefix=raw_token[: len(TOKEN_PREFIX) + PREFIX_SAMPLE_LEN],
            token_hash=hash_token(raw_token),
            expires_at=expires_at,
        )
        return token, raw_token

    def active_for(self, user):
        """Tokens of ``user`` that are neither revoked nor expired."""
        now = timezone.now()
        return self.filter(user=user, revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now),
        )

    def created_last_hour_for(self, user):
        """How many tokens ``user`` has issued in the past hour."""
        since = timezone.now() - timedelta(hours=1)
        return self.filter(user=user, created__gte=since).count()

    def authenticate(self, raw_token):
        """Resolve a raw token to its owner, or raise :class:`UserTokenError`."""
        try:
            token = self.select_related('user').get(token_hash=hash_token(raw_token))
        except self.model.DoesNotExist:
            raise UserTokenError(TokenErrorReason.INVALID) from None

        token.check_usable()
        token.touch()
        return token.user


class UserToken(models.Model):
    """A named, long-lived credential of a user. Only its SHA-256 is stored."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='tokens',
        help_text='The user the token acts as.',
    )
    name = models.CharField(
        max_length=64,
        help_text='Human-readable name, e.g. the client the token is configured in.',
    )
    prefix = models.CharField(
        max_length=16,
        db_index=True,
        help_text='Visible handle: the bpat_ marker plus the first few characters.',
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        help_text='SHA-256 of the token. The token itself is never stored.',
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text='Timestamp of the token creation.',
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the token stops working. NULL means it never expires.',
    )
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time the token authenticated a request, to the minute.',
    )
    revoked_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the token was revoked. NULL means it is still valid.',
    )
    revoked_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='revoked_tokens',
        help_text='Who revoked the token -- its owner, or an admin.',
    )

    objects = UserTokenManager()

    class Meta:
        db_table = 'bublik_user_token'
        ordering: ClassVar[list] = ['-created']
        constraints: ClassVar[list] = [
            models.UniqueConstraint(
                fields=['user', 'name'],
                condition=models.Q(revoked_at__isnull=True),
                name='uniq_active_token_name_per_user',
            ),
        ]

    def __repr__(self):
        # Never include token_hash.
        return (
            f'UserToken(id={self.pk!r}, user={self.user_id!r}, '
            f'name={self.name!r}, prefix={self.prefix!r}, status={self.status!r})'
        )

    @property
    def status(self):
        if self.revoked_at is not None:
            return TokenStatus.REVOKED
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return TokenStatus.EXPIRED
        return TokenStatus.ACTIVE

    def check_usable(self):
        """Raise :class:`UserTokenError` unless the token can authenticate now."""
        if self.revoked_at is not None:
            raise UserTokenError(TokenErrorReason.REVOKED)
        if self.expires_at is not None and self.expires_at <= timezone.now():
            raise UserTokenError(TokenErrorReason.EXPIRED)
        if not self.user.is_active:
            raise UserTokenError(TokenErrorReason.INACTIVE)

    def touch(self):
        """Record use, at most once a minute."""
        now = timezone.now()
        if self.last_used_at is not None and now - self.last_used_at < LAST_USED_RESOLUTION:
            return
        UserToken.objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now

    def revoke(self, revoked_by):
        """Revoke the token."""
        if self.revoked_at is not None:
            return
        self.revoked_at = timezone.now()
        self.revoked_by = revoked_by
        self.save(update_fields=['revoked_at', 'revoked_by'])
