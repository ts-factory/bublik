# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import send_mail
from django.db import transaction
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ViewSet
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from bublik.core.auth import (
    auth_required,
    get_user_by_access_token,
    revoke_refresh_tokens,
)
from bublik.core.exceptions import BublikAPIError, NotFoundError, UserStatusError
from bublik.core.mail import EmailVerificationTokenGenerator, send_verification_link_mail
from bublik.core.shortcuts import build_absolute_uri
from bublik.data.models import User, UserStatus
from bublik.data.serializers import (
    LoginSerializer,
    PasswordChangeSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    UpdateProfileSerializer,
    UpdateUserSerializer,
    UserEmailSerializer,
    UserSerializer,
)
from bublik.interfaces.api_v2.auth.schemas import (
    admin_viewset_schema,
    password_reset_viewset_schema,
    profile_viewset_schema,
    registration_viewset_schema,
    session_viewset_schema,
)
from bublik.settings import SIMPLE_JWT


def set_auth_cookies(response, access_token, refresh_token):
    """Set the access/refresh token cookies, with max_age matching their JWT lifetime."""
    response.set_cookie(
        key='access_token',
        value=str(access_token),
        max_age=int(SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()),
        httponly=True,
        samesite='Strict',
    )
    response.set_cookie(
        key='refresh_token',
        value=str(refresh_token),
        max_age=int(SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()),
        httponly=True,
        samesite='Strict',
    )


__all__ = [
    'AdminViewSet',
    'PasswordResetViewSet',
    'ProfileViewSet',
    'RegistrationViewSet',
    'SessionViewSet',
]


@registration_viewset_schema
class RegistrationViewSet(GenericViewSet):
    serializer_class = RegisterSerializer

    @action(detail=False, methods=['post'])
    def register(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # roll back the user creation if the verification link cannot be sent
        with transaction.atomic():
            user = serializer.save()
            send_verification_link_mail(request, user)
        return Response(
            {'message': 'A verification link has been sent to your email address'},
        )

    @action(
        detail=False,
        methods=['get'],
        url_path=r'register/activate/(?P<user_id_b64>[^/]+)/(?P<token>[^/]+)',
    )
    def activate(self, request, *args, **kwargs):
        email_verification_token = EmailVerificationTokenGenerator()

        user_id_b64 = kwargs['user_id_b64']
        token = kwargs['token']
        try:
            uid = urlsafe_base64_decode(user_id_b64).decode()
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, ObjectDoesNotExist):
            user = None

        if (
            user
            and user.status == UserStatus.PENDING
            and email_verification_token.check_token(user, token)
        ):
            user.activate()
            return Response(
                {'message': 'The email is verified. You are registered.'},
            )

        msg = 'Invalid email verification link'
        raise PermissionDenied(msg)


@session_viewset_schema
class SessionViewSet(ViewSet):
    # like the simplejwt views, don't authenticate the request itself:
    # the session is managed via the tokens passed in the cookies
    authentication_classes = ()
    permission_classes = ()

    @action(detail=False, methods=['post'])
    def login(self, request):
        # authenticate user by email and password
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']

        # create refresh and access token
        refresh_token = TokenPairSerializer.get_token(user)
        access_token = refresh_token.access_token
        response = Response()
        # set cookies
        set_auth_cookies(response, access_token, refresh_token)
        response.data = {
            'user': UserSerializer(user).data,
        }
        return response

    @action(detail=False, methods=['post'])
    def refresh(self, request):
        refresh_token = request.COOKIES.get('refresh_token')
        if not refresh_token:
            msg = 'No refresh token provided'
            raise PermissionDenied(msg)

        try:
            refresh_token = RefreshToken(refresh_token)
            refresh_token.verify()
        except TokenError:
            msg = 'Not a valid refresh token'
            raise PermissionDenied(msg) from None

        user_id = refresh_token['user_id']
        user = User.objects.filter(pk=user_id).first()
        if not user or not user.is_active:
            msg = 'User is not active'
            raise PermissionDenied(msg)

        refresh_token.blacklist()

        new_refresh = RefreshToken.for_user(user)
        new_access = new_refresh.access_token

        response = Response(
            {
                'message': 'Successfully refreshed token',
            },
        )

        set_auth_cookies(response, new_access, new_refresh)

        return response

    @action(detail=False, methods=['post'])
    def logout(self, request):
        # without a refresh token there is no session to end on the server
        refresh_token = request.COOKIES.get('refresh_token')
        if refresh_token:
            try:
                refresh_token = RefreshToken(refresh_token)
                refresh_token.verify()
            except TokenError:
                msg = 'Not a valid refresh token'
                raise PermissionDenied(msg) from None

            refresh_token.blacklist()

        # invalidate old cookies
        response = Response()
        response.delete_cookie('refresh_token')
        response.delete_cookie('access_token')

        response.data = {
            'message': 'Successfully logged out',
        }

        return response


@password_reset_viewset_schema
class PasswordResetViewSet(GenericViewSet):
    def get_serializer_class(self):
        if self.action == 'reset_password':
            return PasswordResetSerializer
        return UserEmailSerializer

    @action(detail=False, methods=['post'])
    def forgot_password(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email=email)
        except ObjectDoesNotExist:
            msg = 'No user found with this email'
            raise PermissionDenied(msg) from None

        if user.status == UserStatus.DEACTIVATED:
            msg = 'Password reset is not available for deactivated users'
            raise PermissionDenied(msg)

        # generate a password reset token
        user_id_b64 = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        # construct the reset link URL
        endpoint = f'v2/auth/forgot_password/password_reset/{user_id_b64}/{token}/'
        reset_link = build_absolute_uri(request, endpoint)

        # send the reset link to the user
        send_mail(
            subject='Password Reset',
            message=f'Click the following link to reset your password: {reset_link}',
            from_email=settings.EMAIL_FROM,
            recipient_list=[user.email],
        )

        return Response(
            {'message': 'Password reset link sent successfully'},
        )

    @action(
        detail=False,
        methods=['put', 'patch'],
        url_path=r'forgot_password/password_reset/(?P<user_id_b64>[^/]+)/(?P<token>[^/]+)',
    )
    def reset_password(self, request, *args, **kwargs):
        user_id_b64 = kwargs['user_id_b64']
        token = kwargs['token']
        try:
            uid = urlsafe_base64_decode(user_id_b64).decode()
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, ObjectDoesNotExist):
            user = None

        if (
            user
            and user.status != UserStatus.DEACTIVATED
            and default_token_generator.check_token(user, token)
        ):
            # validate new password
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            # password reset
            user.set_password(serializer.validated_data['new_password'])
            user.save()

            # end all sessions of the user
            revoke_refresh_tokens(user)

            # the reset link was sent to the user's email, which verifies it
            if user.status == UserStatus.PENDING:
                user.activate()

            return Response(
                {'message': 'Password reset successfully'},
            )

        msg = 'Invalid reset link'
        raise PermissionDenied(msg)


@profile_viewset_schema
class ProfileViewSet(GenericViewSet):
    def get_object(self):
        return get_user_by_access_token(self.request.COOKIES.get('access_token'))

    def get_serializer_class(self):
        if self.action == 'password_reset':
            return PasswordChangeSerializer
        if self.action == 'update_info':
            return UpdateProfileSerializer
        return UserSerializer

    @auth_required(as_admin=False)
    @action(detail=False, methods=['get'])
    def info(self, request):
        user = self.get_object()
        return Response(self.get_serializer(user).data)

    @auth_required(as_admin=False)
    @action(detail=False, methods=['post'])
    def password_reset(self, request):
        user = self.get_object()
        # check current password and validate new password
        serializer = self.get_serializer(data=request.data, context={'user': user})
        serializer.is_valid(raise_exception=True)

        # password reset
        user.set_password(serializer.validated_data['new_password'])
        user.save()

        # end all sessions of the user and start a new one for the current client
        revoke_refresh_tokens(user)
        refresh_token = TokenPairSerializer.get_token(user)
        response = Response(
            {'message': 'Password reset successfully'},
        )
        set_auth_cookies(response, refresh_token.access_token, refresh_token)
        return response

    @auth_required(as_admin=False)
    @action(detail=False, methods=['post'])
    def update_info(self, request):
        user = self.get_object()
        # check if new data is valid
        serializer = self.get_serializer(user, data=request.data)
        serializer.is_valid(raise_exception=True)
        # update user
        updated_user = serializer.save()
        return Response(UserSerializer(updated_user).data)


@admin_viewset_schema
class AdminViewSet(GenericViewSet):
    queryset = User.objects.all()
    # the users list isn't filterable
    filter_backends = ()
    pagination_class = None

    @staticmethod
    def get_user_by_email(email):
        try:
            return User.objects.get(email=email, is_system=False)
        except ObjectDoesNotExist:
            msg = 'No user found with this email'
            raise NotFoundError(msg) from None

    def get_serializer_class(self):
        if self.action == 'create_user':
            return RegisterSerializer
        if self.action == 'update_user':
            return UpdateUserSerializer
        if self.action in ('activate_user', 'deactivate_user'):
            return UserEmailSerializer
        return UserSerializer

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def create_user(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # roll back the user creation if the verification link cannot be sent
        with transaction.atomic():
            user = serializer.save()
            send_verification_link_mail(request, user)
        return Response(
            {'message': "A verification link has been sent to the user's email address"},
        )

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def update_user(self, request):
        # get user to edit
        edit_user = self.get_user_by_email(request.data.get('email'))
        # check if new data is valid
        serializer = self.get_serializer(edit_user, data=request.data)
        serializer.is_valid(raise_exception=True)
        # update user
        updated_user = serializer.save()
        # end all sessions of the user if the password was changed
        if serializer.validated_data.get('password'):
            revoke_refresh_tokens(updated_user)
        return Response(UserSerializer(updated_user).data)

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def activate_user(self, request):
        # get user to activate
        activate_user = self.get_user_by_email(request.data.get('email'))
        # let the deactivated user in again once they verify the email,
        # rolling back if the verification link cannot be sent
        try:
            with transaction.atomic():
                activate_user.reactivate()
                send_verification_link_mail(request, activate_user)
        except UserStatusError as use:
            raise BublikAPIError(use.message) from None
        return Response(
            {'message': "A verification link has been sent to the user's email address"},
        )

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def deactivate_user(self, request):
        # get user to delete
        deactivate_user = self.get_user_by_email(request.data.get('email'))
        admin = get_user_by_access_token(request.COOKIES.get('access_token'))
        # deactivate user and end all their sessions
        try:
            deactivate_user.deactivate(by=admin)
        except UserStatusError as use:
            raise BublikAPIError(use.message) from None
        return Response(
            {'message': 'The user was deactivated'},
        )

    @auth_required(as_admin=True)
    @method_decorator(never_cache)
    def list(self, request):
        # return all users info, except the system user
        return Response(
            self.get_serializer(self.get_queryset().filter(is_system=False), many=True).data,
        )
