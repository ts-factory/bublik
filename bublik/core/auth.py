# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from functools import wraps

from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs, User, UserRoles, UserStatus
from bublik.settings import SIMPLE_JWT


def get_user_info_from_access_token(access_token):
    token_backend = TokenBackend(
        algorithm=SIMPLE_JWT['ALGORITHM'],
        signing_key=SIMPLE_JWT['SIGNING_KEY'],
    )
    return token_backend.decode(access_token, verify=True)


def get_user_by_access_token(access_token):
    try:
        user_info = get_user_info_from_access_token(access_token)
        return User.objects.filter(pk=user_info['user_id'], status=UserStatus.ACTIVE).first()
    except TokenBackendError:
        return None


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
    access_token = request.COOKIES.get('access_token')
    user = get_user_by_access_token(access_token)
    if not user:
        msg = 'Not Authenticated'
        raise PermissionDenied(msg)
    # check if user is admin
    if as_admin and UserRoles.ADMIN not in user.roles:
        msg = 'You are not authorized to perform this action'
        raise PermissionDenied(msg)
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
        not_permission_required_actions = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'NOT_PERMISSION_REQUIRED_ACTIONS',
            project_id=project_id,
        )
        if action not in not_permission_required_actions:
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
