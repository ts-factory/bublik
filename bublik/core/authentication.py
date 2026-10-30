# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
DRF authentication classes, named in ``REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES']``.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed


class PersonalTokenAuthentication(BaseAuthentication):
    """Authenticates ``Bearer bpat_...``; any other header is left to the next class.

    It must be listed before ``JWTAuthentication``, which claims every Bearer
    header and refuses a personal token as a malformed JWT.
    """

    def authenticate(self, request):
        # Imported here: DRF loads this class while bublik.data.models is
        # still being imported.
        from bublik.core.auth import (  # noqa: PLC0415
            get_bearer_token,
            get_user_by_personal_token,
        )
        from bublik.data.models import UserTokenError  # noqa: PLC0415

        raw_token = get_bearer_token(request.headers.get('Authorization'))
        if raw_token is None:
            return None
        try:
            user = get_user_by_personal_token(raw_token)
        except UserTokenError as exc:
            raise AuthenticationFailed(exc.message) from None
        # Spares get_request_user() a second lookup, and tells
        # require_login_session() how the caller authenticated.
        request._bublik_user = user
        request._bublik_by_token = True
        return user, None

    def authenticate_header(self, request):
        # The challenge JWTAuthentication sends, so a refusal stays a 401.
        return 'Bearer realm="api"'


class PersonalTokenScheme(OpenApiAuthenticationExtension):
    """Describes :class:`PersonalTokenAuthentication` in the OpenAPI schema."""

    target_class = 'bublik.core.authentication.PersonalTokenAuthentication'
    name = 'personalTokenAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'bpat_...',
            'description': 'A personal access token issued at /api/v2/auth/tokens/.',
        }
