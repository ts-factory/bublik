# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.data.serializers import (
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    UpdateProfileSerializer,
    UserEmailSerializer,
    UserSerializer,
)
from bublik.interfaces.api_v2.auth.serializers import (
    AdminUpdateUserRequestSerializer,
    AuthMessageResponseSerializer,
    LoginResponseSerializer,
)
from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer


AUTH_TAG = 'Authentication'


registration_viewset_schema = extend_schema_view(
    register=extend_schema(
        summary='Register user',
        description="""
        Create a pending user and send an email verification link
        to the passed email address. The user is activated once the link is followed.
        """,
        request=RegisterSerializer,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='User was created and the verification link was sent',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid registration data was provided',
            ),
        },
        tags=[AUTH_TAG],
    ),
    activate=extend_schema(
        summary='Activate user',
        description="""
        Verify the user's email address by the link sent on registration
        and activate the user.
        """,
        request=None,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='Email address was verified and the user was activated',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Email verification link is invalid',
            ),
        },
        tags=[AUTH_TAG],
    ),
)


session_viewset_schema = extend_schema_view(
    login=extend_schema(
        summary='Log in',
        description="""
        Authenticate the user by email and password and start a session:
        the access and refresh tokens are set in HttpOnly cookies.
        """,
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(
                response=LoginResponseSerializer,
                description='User was successfully logged in',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Email or password is not a string',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Credentials are missing or invalid, or the email is not verified',
            ),
        },
        tags=[AUTH_TAG],
    ),
    refresh=extend_schema(
        summary='Refresh session tokens',
        description="""
        Issue new access and refresh tokens by the refresh token passed in the cookie.
        The passed refresh token is blacklisted, the new tokens are set in HttpOnly cookies.
        """,
        request=None,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='Tokens were successfully refreshed',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Refresh token is missing or invalid, or the user is deactivated',
            ),
        },
        tags=[AUTH_TAG],
    ),
    logout=extend_schema(
        summary='Log out',
        description="""
        End the session: the refresh token passed in the cookie is blacklisted,
        the token cookies are deleted. If no refresh token is passed,
        the cookies are just deleted.
        """,
        request=None,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='User was successfully logged out',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Refresh token is invalid',
            ),
        },
        tags=[AUTH_TAG],
    ),
)


password_reset_viewset_schema = extend_schema_view(
    forgot_password=extend_schema(
        summary='Request password reset',
        description="""
        Send a password reset link to the passed email address.
        """,
        request=UserEmailSerializer,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='Password reset link was sent',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Email is invalid or no user with this email was found',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Password reset is not available for deactivated users',
            ),
        },
        tags=[AUTH_TAG],
    ),
    reset_password=extend_schema(
        summary='Reset password',
        description="""
        Set a new password for the user by the link sent on the password reset request.
        The sessions of the user can no longer be renewed
        and end when their access token expires. A user waiting for email
        verification is activated, since the link was sent to their email.
        """,
        request=PasswordResetSerializer,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='Password was successfully reset',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Passwords were not provided, are invalid or do not match',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Password reset link is invalid or the user is deactivated',
            ),
        },
        tags=[AUTH_TAG],
    ),
)


profile_viewset_schema = extend_schema_view(
    info=extend_schema(
        summary='Get current user',
        description="""
        Return the information about the user authenticated by the access token cookie.
        """,
        responses={
            200: OpenApiResponse(
                response=UserSerializer,
                description='User information was successfully retrieved',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='User is not authenticated',
            ),
        },
        tags=[AUTH_TAG],
    ),
    password_reset=extend_schema(
        summary='Change current user password',
        description="""
        Change the password of the current user, which requires their current password.
        Other sessions of the user can no longer be renewed and end when their
        access token expires, the current one gets new tokens set in HttpOnly cookies.
        """,
        request=PasswordChangeSerializer,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='Password was successfully changed',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Passwords were not provided, are invalid or do not match',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='User is not authenticated or the current password is invalid',
            ),
        },
        tags=[AUTH_TAG],
    ),
    update_info=extend_schema(
        summary='Update current user',
        description="""
        Update the first name and last name of the current user by the passed data.
        Other fields are rejected.
        """,
        request=UpdateProfileSerializer,
        responses={
            200: OpenApiResponse(
                response=UserSerializer,
                description='User was successfully updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid user data was provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='User is not authenticated',
            ),
        },
        tags=[AUTH_TAG],
    ),
)


admin_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List users',
        description="""
        Return all users. Requires administrator's role.
        """,
        responses={
            200: OpenApiResponse(
                response=UserSerializer(many=True),
                description='Users were successfully retrieved',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to list users',
            ),
        },
        tags=[AUTH_TAG],
    ),
    create_user=extend_schema(
        summary='Create user',
        description="""
        Create a pending user and send an email verification link
        to the user's email address. The user is activated once the link is followed.
        Requires administrator's role.
        """,
        request=RegisterSerializer,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='User was created and the verification link was sent',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid user data was provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to create users',
            ),
        },
        tags=[AUTH_TAG],
    ),
    update_user=extend_schema(
        summary='Update user',
        description="""
        Update the first name, last name and password of the user
        identified by the passed email by the passed data.
        If the password is changed, the sessions of the user can no longer be renewed
        and end when their access token expires.
        Requires administrator's role.
        """,
        request=AdminUpdateUserRequestSerializer,
        responses={
            200: OpenApiResponse(
                response=UserSerializer,
                description='User was successfully updated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Invalid user data was provided',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to update users',
            ),
        },
        tags=[AUTH_TAG],
    ),
    deactivate_user=extend_schema(
        summary='Deactivate user',
        description="""
        Deactivate the user identified by the passed email and end all their sessions.
        Requires administrator's role.
        """,
        request=UserEmailSerializer,
        responses={
            200: OpenApiResponse(
                response=AuthMessageResponseSerializer,
                description='User was successfully deactivated',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Users cannot deactivate themselves or the system user',
            ),
            403: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Administrator privileges are required to deactivate users',
            ),
        },
        tags=[AUTH_TAG],
    ),
)
