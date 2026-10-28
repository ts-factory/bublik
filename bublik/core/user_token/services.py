# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import Throttled, ValidationError

from bublik.core.exceptions import NotFoundError
from bublik.core.user_token.dto import IssuedUserTokenDTO, UserTokenDTO
from bublik.data.models import User, UserToken
from bublik.data.models.user_token import RATE_LIMIT_WINDOW


if TYPE_CHECKING:
    from django.db.models import QuerySet


EXPIRY_CHOICES = (7, 30, 90, None)
MAX_TOKENS_PER_USER = 20
MAX_TOKENS_CREATED_PER_HOUR = 10
DUPLICATE_NAME = 'You already have a token with this name. Revoke it first.'


class UserTokenService:
    """
    Issuing, listing and revoking personal access tokens.
    """

    @staticmethod
    def to_dto(token: UserToken) -> UserTokenDTO:
        """
        Convert a token model instance into a DTO.

        Args:
            token: The token model instance

        Returns:
            UserTokenDTO, carrying no value and no hash
        """
        return UserTokenDTO(
            id=token.id,
            name=token.name,
            prefix=token.prefix,
            status=token.status,
            owner=token.user.email,
            created=token.created,
            expires_at=token.expires_at,
            last_used_at=token.last_used_at,
            revoked_at=token.revoked_at,
            revoked_by=token.revoked_by.email if token.revoked_by else None,
        )

    @staticmethod
    def tokens_of(user: User) -> QuerySet[UserToken]:
        """
        The tokens belonging to one user, newest first, ready for :meth:`to_dto`.

        Args:
            user: The owner

        Returns:
            QuerySet of UserToken, for the view to page through
        """
        return UserToken.objects.filter(user=user).select_related('user', 'revoked_by')

    @staticmethod
    def all_tokens() -> QuerySet[UserToken]:
        """
        Every user's tokens, newest first, for the admin view.

        Returns:
            QuerySet of UserToken, for the view to page through
        """
        return UserToken.objects.select_related('user', 'revoked_by').all()

    @staticmethod
    def validate_issuable(user: User, name: str, expires_in: int | None) -> str:
        """
        Check that ``user`` may issue this token right now.

        Args:
            user: The prospective owner
            name: The requested name
            expires_in: Lifetime in days, or None for a token that never expires

        Returns:
            The normalised name

        Raises:
            ValidationError: on a bad expiry or name, or at the active token limit
            Throttled: if too many tokens were created in the past hour
        """
        if expires_in not in EXPIRY_CHOICES:
            # As the API spells them: a never-expiring token is `null`.
            offered = ', '.join('null' if c is None else str(c) for c in EXPIRY_CHOICES)
            msg = f'Unsupported expiry. Possible are: {offered}'
            raise ValidationError(msg)

        name = name.strip()
        if not name:
            msg = 'A token needs a name'
            raise ValidationError(msg)

        max_length = UserToken._meta.get_field('name').max_length
        if len(name) > max_length:
            msg = f'A token name can be at most {max_length} characters'
            raise ValidationError(msg)

        # Expired tokens keep their name until revoked, as in the DB constraint.
        if UserToken.objects.filter(user=user, name=name, revoked_at__isnull=True).exists():
            raise ValidationError(DUPLICATE_NAME)

        if UserToken.objects.active_for(user).count() >= MAX_TOKENS_PER_USER:
            msg = (
                f'You have reached the limit of {MAX_TOKENS_PER_USER} active tokens. '
                'Revoke one before issuing another.'
            )
            raise ValidationError(msg)

        # A DB count, not a DRF throttle: the default cache is a DummyCache.
        recent = UserToken.objects.created_last_hour_for(user)
        if recent.count() >= MAX_TOKENS_CREATED_PER_HOUR:
            # Retry-After: when the oldest token of the hour leaves the window.
            # None only with a limit of zero, which never lifts within it.
            oldest = recent.first()
            now = timezone.now()
            wait = (oldest.created if oldest else now) + RATE_LIMIT_WINDOW - now
            msg = 'Too many tokens created recently. Try again later.'
            raise Throttled(wait=max(math.ceil(wait.total_seconds()), 1), detail=msg)

        return name

    @staticmethod
    def issue(user: User, name: str, *, expires_in: int | None) -> IssuedUserTokenDTO:
        """
        Issue a token and return it with its value, which is not stored.

        Args:
            user: The owner the token will act as
            name: Human-readable name for the token
            expires_in: Lifetime in days (7, 30 or 90), or None for never. No
                default: a token that never expires must be asked for.

        Returns:
            IssuedUserTokenDTO carrying the token and its only copy of the value

        Raises:
            ValidationError: on a bad expiry or name, or at the active token limit
            Throttled: if too many tokens were created in the past hour
        """
        # Lock the user so concurrent requests cannot exceed the limits.
        try:
            with transaction.atomic():
                User.objects.select_for_update().get(pk=user.pk)
                name = UserTokenService.validate_issuable(user, name, expires_in)
                token, value = UserToken.objects.issue(
                    user=user,
                    name=name,
                    expires_in_days=expires_in,
                )
        except IntegrityError:
            # Lost a race on the name; an expected 400, not an error to log.
            raise ValidationError(DUPLICATE_NAME) from None
        return IssuedUserTokenDTO(token=UserTokenService.to_dto(token), value=value)

    @staticmethod
    def revoke(token_id: int, acting_user: User, as_admin: bool = False) -> UserTokenDTO:
        """
        Revoke a token. The row is kept to record who revoked it.

        Args:
            token_id: The ID of the token to revoke
            acting_user: Who is revoking it
            as_admin: Whether the caller may revoke another user's token

        Returns:
            UserTokenDTO for the revoked token

        Raises:
            NotFoundError: if no such token, or it belongs to someone else and
                the caller is not an admin
        """
        try:
            token = UserToken.objects.select_related('user', 'revoked_by').get(pk=token_id)
        except (UserToken.DoesNotExist, TypeError, ValueError):
            msg = f'Token {token_id} not found'
            raise NotFoundError(msg) from None

        # Report someone else's token as missing to not reveal that it exists.
        if token.user_id != acting_user.id and not as_admin:
            msg = f'Token {token_id} not found'
            raise NotFoundError(msg)

        token.revoke(revoked_by=acting_user)
        return UserTokenService.to_dto(token)
