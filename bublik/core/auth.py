# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from functools import wraps

from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.request import Request
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from bublik.core.config.services import ConfigServices
from bublik.data.models import (
    TOKEN_PREFIX,
    GlobalConfigs,
    User,
    UserRoles,
    UserToken,
    UserTokenError,
)


NOT_AUTHENTICATED = 'Not Authenticated'
NOT_AUTHORIZED = 'You are not authorized to perform this action'

# Marks a request whose caller has not been resolved yet; None is a resolved anonymous.
_UNRESOLVED = object()


def get_user_info_from_access_token(access_token):
    token_backend = TokenBackend(
        algorithm=settings.SIMPLE_JWT['ALGORITHM'],
        signing_key=settings.SIMPLE_JWT['SIGNING_KEY'],
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
    return UserToken.objects.authenticate(raw_token)


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

    A refused token raises ``AuthenticationFailed`` with the reason.
    """
    user = getattr(request, '_bublik_user', _UNRESOLVED)
    if user is _UNRESOLVED:
        authorization = request.headers.get('Authorization')
        try:
            user = resolve_user(
                authorization=authorization,
                access_token=request.COOKIES.get('access_token'),
            )
        except UserTokenError as exc:
            raise AuthenticationFailed(exc.message) from None
        request._bublik_user = user
        request._bublik_by_token = user is not None and bool(get_bearer_token(authorization))
    return user


def require_login_session(request):
    """Refuse a request authenticated with a personal access token.

    For the actions that would let a leaked token outlive itself: minting or
    revoking tokens, and administering users, since setting a user's password
    gives the token holder a login session. Decided from how the caller was
    resolved, not by looking at the header again.
    """
    get_request_user(request)
    if getattr(request, '_bublik_by_token', False):
        msg = 'This action needs a login session; access tokens are not accepted'
        raise PermissionDenied(msg)


def is_admin(user):
    return user is not None and user.roles == UserRoles.ADMIN


def revoke_refresh_tokens(user):
    """
    Blacklists all outstanding refresh tokens of the user.
    """
    tokens = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in tokens],
        ignore_conflicts=True,
    )


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


def require_user(request, as_admin=False):
    """
    Raise PermissionDenied unless the request comes from an authenticated
    user (an administrator if `as_admin`).
    """
    user = get_request_user(request)
    if not user:
        raise PermissionDenied(NOT_AUTHENTICATED)
    # check if user is admin
    if as_admin and not is_admin(user):
        raise PermissionDenied(NOT_AUTHORIZED)
    return user


def auth_required(as_admin=False):
    def decorator(function):
        @wraps(function)
        def wrapper(*args, **kwargs):
            request = get_request(*args, **kwargs)
            if not request:
                # handle regular function call without a request object
                msg = 'Wrong request'
                raise PermissionDenied(msg)
            require_user(request, as_admin=as_admin)
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


def action_permitted(action, user, project_ids=()):
    """Whether ``user`` may perform ``action`` in every one of the projects.

    The same rule as `check_action_permission_for_projects`, for callers
    without a request: with no projects the global settings apply.
    """
    if all(action_is_open(action, project_id) for project_id in set(project_ids) or {None}):
        return True
    return is_admin(user) and user.is_active


def check_action_permission_for_projects(request, action, project_ids):
    """
    Check the action against the projects it touches.

    The action is allowed without authorization only if the settings of each
    of the projects allow it, otherwise the request must come from an
    administrator. With no projects the global settings apply.

    Use it in place of `check_action_permission` when the projects are told
    by the objects the request changes: the `project` query param is chosen
    by the client, so it can't be trusted to name them.
    """
    for project_id in set(project_ids) or {None}:
        if not action_is_open(action, project_id):
            require_user(request, as_admin=True)
            return


def check_action_permission(action):
    """
    Check if the action requires permission.

    The settings of the project from the `project` query param apply, which
    suits requests that merely filter by project. A request that changes
    particular objects should use `check_action_permission_for_projects` instead.
    """

    def wrapper(func):
        @wraps(func)
        def inner(self, request, *args, **kwargs):
            check_action_permission_for_projects(
                request,
                action,
                [request.query_params.get('project')],
            )
            return func(self, request, *args, **kwargs)

        return inner

    return wrapper
