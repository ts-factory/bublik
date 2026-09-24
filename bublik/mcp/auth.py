# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Authenticating MCP callers with Bublik personal access tokens.

A token is optional: without one the request stays anonymous and read tools
keep working. A present but bad token is refused with a 401. The in-process
chat agent binds its user with :func:`bublik.core.auth.bind_acting_user`.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import wraps
import inspect

from asgiref.sync import sync_to_async
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token
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

from bublik.core.auth import (
    action_permitted,
    current_acting_user,
    get_bearer_token,
    get_user_by_personal_token,
    is_admin,
)
from bublik.data.models import User, UserTokenError


MISSING_TOKEN = (
    'This tool requires a Bublik personal access token. Send it as '
    'Authorization: Bearer bpat_... -- create one under Settings > Access tokens.'
)
MALFORMED_HEADER = 'Malformed Authorization header; expected "Bearer bpat_...".'
NOT_ADMIN = 'This tool is restricted to Bublik administrators.'


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


def current_mcp_user() -> User:
    """The calling user from the access token or the bound user, or ``None``."""
    token = get_access_token()
    if token is None:
        return current_acting_user()
    return User.objects.get(pk=token.claims['user_id'])


# The caller mcp_auth_required authorized for the running tool.
_caller: ContextVar[User | None] = ContextVar('bublik_mcp_caller', default=None)


def mcp_caller() -> User | None:
    """The caller authorized by :func:`mcp_auth_required` for the running tool."""
    return _caller.get()


def _authorize(action, admin, bound_args):
    """Resolve and authorize the caller, or raise a ToolError explaining why not."""
    user = current_mcp_user()
    if user is None:
        raise ToolError(MISSING_TOKEN)
    if admin and not is_admin(user):
        raise ToolError(NOT_ADMIN)
    if action is not None:
        project_id = bound_args.get('project_id')
        if not action_permitted(action, user, project_id):
            msg = f'{user.email} is not allowed to perform "{action}" in this project.'
            raise ToolError(msg)
    return user


def mcp_auth_required(action=None, admin=False):
    """Require an access token on an MCP tool, plus ``action`` permission or admin.

    Not ``mcp.tool(auth=...)``: FastMCP reports its failures as "Unknown tool".
    """

    def decorator(function):
        signature = inspect.signature(function)

        @wraps(function)
        async def wrapper(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            user = await sync_to_async(_authorize)(action, admin, bound.arguments)
            token = _caller.set(user)
            try:
                return await function(*args, **kwargs)
            finally:
                _caller.reset(token)

        return wrapper

    return decorator
