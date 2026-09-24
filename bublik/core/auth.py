# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from functools import wraps

from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError

from bublik.core.config.services import ConfigServices
from bublik.core.user_token.services import UserTokenService
from bublik.data.models import (
    TOKEN_PREFIX,
    GlobalConfigs,
    User,
    UserRoles,
    UserTokenError,
)
from bublik.settings import SIMPLE_JWT


NOT_AUTHENTICATED = 'Not Authenticated'
NOT_AUTHORIZED = 'You are not authorized to perform this action'


def get_user_info_from_access_token(access_token):
    token_backend = TokenBackend(
        algorithm=SIMPLE_JWT['ALGORITHM'],
        signing_key=SIMPLE_JWT['SIGNING_KEY'],
    )
    return token_backend.decode(access_token, verify=True)


def get_user_by_access_token(access_token):
    """Resolve the user behind a JWT access token, or ``None`` if invalid or inactive."""
    if not access_token:
        return None
    try:
        user_info = get_user_info_from_access_token(access_token)
        user = User.objects.get(pk=user_info['user_id'])
    except (TokenBackendError, User.DoesNotExist):
        return None
    return user if user.is_active else None


def get_user_by_personal_token(raw_token):
    """Resolve the owner of a personal access token, or raise ``UserTokenError``."""
    return UserTokenService.authenticate(raw_token)


def get_bearer_token(authorization):
    """The personal access token in an Authorization header, if there is one."""
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(' ')
    if scheme.lower() != 'bearer':
        return None
    credentials = credentials.strip()
    return credentials if credentials.startswith(TOKEN_PREFIX) else None


def resolve_user(authorization=None, access_token=None):
    """Resolve a caller from a bearer token or the login cookie, or ``None``.

    A bearer token takes precedence, and a bad one raises instead of falling
    back to the cookie.
    """
    raw_token = get_bearer_token(authorization)
    if raw_token:
        return get_user_by_personal_token(raw_token)
    return get_user_by_access_token(access_token)


def get_request_user(request):
    """:func:`resolve_user` for a DRF request, memoised on the request.

    A refused token raises ``PermissionDenied`` with the reason.
    """
    user = getattr(request, '_bublik_user', None)
    if user is None:
        try:
            user = resolve_user(
                authorization=request.headers.get('Authorization'),
                access_token=request.COOKIES.get('access_token'),
            )
        except UserTokenError as exc:
            raise PermissionDenied(exc.message) from None
        request._bublik_user = user
    return user


def is_admin(user):
    return user is not None and user.roles == UserRoles.ADMIN


# The user that code outside an HTTP request (the chat agent) acts as.
_acting_user_id: ContextVar[int | None] = ContextVar('bublik_acting_user_id', default=None)


@contextlib.contextmanager
def bind_acting_user(user_id):
    """Run the enclosed block as ``user_id``.

    Spawned tasks and ``sync_to_async`` threads inherit the binding.
    """
    token = _acting_user_id.set(user_id)
    try:
        yield
    finally:
        _acting_user_id.reset(token)


def current_acting_user():
    """The bound user, or ``None`` when nobody is bound or they are inactive."""
    user_id = _acting_user_id.get()
    if user_id is None:
        return None
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return None
    return user if user.is_active else None


def get_request(*args, **kwargs):
    # check if 'request' is present in the keyword arguments
    if 'request' in kwargs and isinstance(kwargs['request'], Request):
        request = kwargs['request']
    # check if the first argument is an instance of Request
    elif args and isinstance(args[0], Request):
        request = args[0]
    # check if the first argument has a 'request' attribute (ViewSet case)
    elif hasattr(args[0], 'request') and isinstance(args[0].request, Request):
        request = args[0].request
    else:
        return None
    return request


def auth_required(as_admin=False):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            request = get_request(*args, **kwargs)
            if not request:
                # handle regular function call without a request object
                msg = 'Wrong request'
                raise PermissionDenied(msg)
            user = get_request_user(request)
            if not user:
                raise PermissionDenied(NOT_AUTHENTICATED)
            # check if user is admin
            if as_admin and not is_admin(user):
                raise PermissionDenied(NOT_AUTHORIZED)
            return function(*args, **kwargs)

        return wrapper

    return decorator


def action_is_open(action, project_id=None):
    """Whether the project lets anyone perform ``action``."""
    return action in ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        'NOT_PERMISSION_REQUIRED_ACTIONS',
        project_id=project_id,
    )


def action_permitted(action, user, project_id=None):
    """Whether ``user`` may perform ``action`` in this project."""
    return action_is_open(action, project_id) or (is_admin(user) and user.is_active)


def check_action_permission(action):
    """
    Check if the action requires permission.
    """

    def wrapper(func):
        @wraps(func)
        def inner(self, request, *args, **kwargs):
            if action_is_open(action, request.query_params.get('project')):
                return func(self, request, *args, **kwargs)
            return auth_required(as_admin=True)(func)(self, request, *args, **kwargs)

        return inner

    return wrapper
