# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Authenticating MCP callers with Bublik personal access tokens.

A token is optional: without one the request stays anonymous and read tools
keep working. A present but bad token is refused with a 401.
"""

from __future__ import annotations

from asgiref.sync import sync_to_async
from fastmcp.server.auth import AccessToken
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.responses import JSONResponse

from bublik.core.auth import get_bearer_token, get_user_by_personal_token
from bublik.data.models import User, UserTokenError


MALFORMED_HEADER = 'Malformed Authorization header; expected "Bearer bpat_...".'


def _access_token_for(user: User, raw_token: str) -> AccessToken:
    """The FastMCP identity for a resolved caller.

    ``expires_at`` is unset: the token is checked against the DB on every request.
    """
    return AccessToken(
        token=raw_token,
        client_id=str(user.id),
        scopes=[],
        expires_at=None,
        claims={'user_id': user.id, 'email': user.email, 'roles': user.roles},
    )


class BublikBearerAuthBackend(AuthenticationBackend):
    """Like the stock ``BearerAuthBackend``, but refuses a bad token instead of
    treating the request as anonymous.
    """

    async def authenticate(self, conn):
        header = conn.headers.get('authorization')
        if not header:
            return None

        raw_token = get_bearer_token(header)
        if raw_token is None:
            raise AuthenticationError(MALFORMED_HEADER)

        try:
            user = await sync_to_async(get_user_by_personal_token)(raw_token)
        except UserTokenError as exc:
            raise AuthenticationError(exc.message) from None

        return AuthCredentials([]), AuthenticatedUser(_access_token_for(user, raw_token))


def on_auth_error(conn, exc) -> JSONResponse:
    """Render a refused credential as a JSON 401."""
    description = str(exc)
    quoted = description.replace('\\', '\\\\').replace('"', '\\"')
    return JSONResponse(
        {'error': 'invalid_token', 'error_description': description},
        status_code=401,
        headers={
            'WWW-Authenticate': (f'Bearer error="invalid_token", error_description="{quoted}"'),
        },
    )


def build_auth_middleware() -> list[Middleware]:
    """The Starlette middleware that resolves a caller, without requiring one.

    Not ``FastMCP(auth=...)``, which rejects every anonymous request.
    """
    return [
        Middleware(
            AuthenticationMiddleware,
            backend=BublikBearerAuthBackend(),
            on_error=on_auth_error,
        ),
        Middleware(AuthContextMiddleware),
    ]
