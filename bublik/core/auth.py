# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from functools import wraps

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError

from bublik.data.models import User, UserRoles
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
        user = User.objects.get(pk=user_info['user_id'])
        return user
    except TokenBackendError:
        return None


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
                return Response(
                    {'message': 'Wrong request'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            access_token = request.COOKIES.get('access_token')
            user = get_user_by_access_token(access_token)
            if not user:
                return Response(
                    {'message': 'Not Authenticated'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # check if user is admin
            if as_admin and UserRoles.ADMIN not in user.roles:
                return Response(
                    {'message': 'You are not authorized to perform this action'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            return function(*args, **kwargs)
        return wrapper
    return decorator
