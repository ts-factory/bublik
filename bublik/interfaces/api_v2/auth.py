# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from django.contrib.auth import authenticate
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import send_mail
from django.utils.decorators import method_decorator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from django.views.decorators.cache import never_cache
from rest_framework import generics, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from bublik.core.auth import auth_required, get_user_by_access_token, get_user_info_from_access_token
from bublik.core.mail import EmailVerificationTokenGenerator, send_verification_link_mail
from bublik.core.shortcuts import build_absolute_uri
from bublik.data.models import User
from bublik.data.serializers import (
    PasswordResetSerializer,
    RegisterSerializer,
    TokenPairSerializer,
    UpdateUserSerializer,
    UserEmailSerializer,
    UserSerializer,
)


__all__ = [
    'RegisterView',
    'LogInView',
    'ProfileViewSet',
    'RefreshTokenView',
    'LogOutView',
    'ForgotPasswordView',
    'ForgotPasswordResetView',
    'AdminViewSet',
]


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validate(request.data)
        user = serializer.create(request.data)
        send_verification_link_mail(request, user)
        return Response(
            {'message': 'A verification link has been sent to your email address'},
            status=status.HTTP_200_OK,
        )


class ActivateView(APIView):
    def get(self, request, *args, **kwargs):
        email_verification_token = EmailVerificationTokenGenerator()

        user_id_b64 = kwargs['user_id_b64']
        token = kwargs['token']
        try:
            uid = urlsafe_base64_decode(user_id_b64).decode()
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, ObjectDoesNotExist):
            user = None

        if user and email_verification_token.check_token(user, token):
            user.is_active = True
            user.save()
            return Response(
                {'message': 'The email is verified. You are registered.'},
                status=status.HTTP_200_OK,
            )
        return Response(
            {'message': 'Invalid email verification link'},
            status=status.HTTP_403_FORBIDDEN,
        )


class LogInView(TokenObtainPairView):
    serializer_class = TokenPairSerializer

    def post(self, request):
        # get email and password from request
        email = request.data.get('email')
        password = request.data.get('password')

        # check if email and password are provided
        if not email or not password:
            return Response(
                {'message': 'Please provide both email and password'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # authenticate user
        user = authenticate(email=email, password=password)

        # check if user is valid
        if not user:
            return Response(
                {'message': 'Invalid credentials'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # create refresh and access token
        refresh_token = self.serializer_class.get_token(user)
        access_token = refresh_token.access_token
        response = Response()
        # set cookies
        response.set_cookie(
            key='refresh_token',
            value=str(refresh_token),
            httponly=True,
            samesite='Strict',
        )
        response.set_cookie(
            key='access_token',
            value=str(access_token),
            httponly=True,
            samesite='Strict',
        )
        response.data = {
            'user': UserSerializer(user).data,
        }
        response.status_code = status.HTTP_200_OK
        return response


class ProfileViewSet(GenericViewSet):
    def get_serializer_class(self):
        if self.action == 'password_reset':
            return PasswordResetSerializer
        if self.action == 'update_info':
            return UpdateUserSerializer
        return UserSerializer

    @auth_required(as_admin=False)
    @action(detail=False, methods=['get'])
    def info(self, request):
        # get access token from cookies
        access_token = request.COOKIES.get('access_token')
        user = get_user_by_access_token(access_token)
        serializer_class = self.get_serializer_class()
        return Response(serializer_class(user).data, status=status.HTTP_200_OK)

    @auth_required(as_admin=False)
    @action(detail=False, methods=['post'])
    def password_reset(self, request):
        # get access token from cookies
        access_token = request.COOKIES.get('access_token')
        user = get_user_by_access_token(access_token)
        # check current password and validate new password
        passwords = request.data
        serializer_class = self.get_serializer_class()
        serializer = serializer_class(data=passwords)
        serializer.current_password_check(user, passwords['current_password'])
        serializer.validate_passwords(passwords)

        # password reset
        user.set_password(passwords['new_password'])
        user.save()

        # blacklist old refresh tokens
        RefreshToken.for_user(user).blacklist()

        return Response(
            {'message': 'Password reset successfully'},
            status=status.HTTP_200_OK,
        )

    @auth_required(as_admin=False)
    @action(detail=False, methods=['post'])
    def update_info(self, request):
        # get access token from cookies
        access_token = request.COOKIES.get('access_token')
        user = get_user_by_access_token(access_token)
        serializer_class = self.get_serializer_class()
        # check if new data is valid
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        # update user
        updated_user = serializer.update(
            user=user,
            data=request.data,
        )
        return Response(UserSerializer(updated_user).data, status=status.HTTP_200_OK)


class RefreshTokenView(TokenRefreshView):
    serializer_class = TokenRefreshSerializer

    def post(self, request):
        try:
            # get refresh token from request cookies
            refresh_token = request.COOKIES.get('refresh_token')
            refresh_token = RefreshToken(refresh_token)

            # verify refresh token
            try:
                refresh_token.verify()
            except TokenError:
                return Response(
                    {'message': 'Not a valid refresh token'},
                    status=status.HTTP_403_FORBIDDEN,
                )
            access_token = refresh_token.access_token

            user = get_user_by_access_token(access_token)
            if not user:
                return Response(
                    {'message': 'Not Authenticated'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            # blacklist refresh token
            refresh_token.blacklist()

            # create new refresh and access tokens for user
            refresh_token = RefreshToken.for_user(user)
            access_token = refresh_token.access_token
            response = Response()

            # invalidate old cookies and set new ones
            response.set_cookie(
                key='access_token',
                value=str(access_token),
                httponly=True,
                samesite='Strict',
            )
            response.set_cookie(
                key='refresh_token',
                value=str(refresh_token),
                httponly=True,
                samesite='Strict',
            )
            response.data = {
                'message': 'Successfully refreshed token',
            }
            response.status_code = status.HTTP_200_OK
            return response
        except Exception as e:
            return Response(
                {'message': 'Refresh process failed', 'error': str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )


class LogOutView(APIView):
    def post(self, request):
        try:
            refresh_token = request.COOKIES.get('refresh_token')
            refresh_token = RefreshToken(refresh_token)
            try:
                refresh_token.verify()
            except TokenError:
                return Response(
                    {'message': 'Not a valid refresh token'},
                    status=status.HTTP_403_FORBIDDEN,
                )

            refresh_token.blacklist()

            # invalidate old cookies
            response = Response()
            response.delete_cookie('refresh_token')
            response.delete_cookie('access_token')

            response.data = {
                'message': 'Successfully logged out',
            }
            response.status_code = status.HTTP_200_OK

            return response
        except Exception as e:
            return Response(
                {'message': 'Logout process failed', 'error': str(e)},
                status=status.HTTP_403_FORBIDDEN,
            )


class ForgotPasswordView(generics.CreateAPIView):
    serializer_class = UserEmailSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']

        try:
            user = User.objects.get(email=email)
        except ObjectDoesNotExist:
            return Response(
                {'message': 'No user found with this email'},
                status=status.HTTP_403_FORBIDDEN,
            )

        # generate a password reset token
        user_id_b64 = urlsafe_base64_encode(force_bytes(user.pk))
        token_serializer = TokenPairSerializer()
        access_token = token_serializer.get_token(user).access_token

        # construct the reset link URL
        endpoint = f'v2/auth/forgot_password/password_reset/{user_id_b64}/{access_token}/'
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
            status=status.HTTP_200_OK,
        )


class ForgotPasswordResetView(generics.UpdateAPIView):
    serializer_class = PasswordResetSerializer

    def update(self, request, *args, **kwargs):
        user_id_b64 = kwargs['user_id_b64']
        access_token = kwargs['token']
        try:
            uid = urlsafe_base64_decode(user_id_b64).decode()
            user = User.objects.get(pk=uid)
        except (TypeError, ValueError, OverflowError, ObjectDoesNotExist):
            user = None

        if user and get_user_info_from_access_token(access_token):
            # validate new password
            new_passwords = request.data
            serializer = self.serializer_class(data=new_passwords)
            serializer.validate_passwords(new_passwords)
            # password reset
            user.set_password(new_passwords['new_password'])
            user.save()

            # blacklist old refresh tokens
            RefreshToken.for_user(user).blacklist()

            return Response(
                {'message': 'Password reset successfully'},
                status=status.HTTP_200_OK,
            )
        return Response(
            {'message': 'Invalid reset link'},
            status=status.HTTP_403_FORBIDDEN,
        )


class AdminViewSet(GenericViewSet):
    def get_serializer_class(self):
        if self.action == 'create_user':
            return RegisterSerializer
        if self.action == 'update_user':
            return UpdateUserSerializer
        if self.action == 'deactivate_user':
            return UserEmailSerializer
        return UserSerializer

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def create_user(self, request):
        serializer_class = self.get_serializer_class()
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validate(request.data)
        user = serializer.create(request.data)
        send_verification_link_mail(request, user)
        return Response(
            {'message': 'A verification link has been sent to the user\'s email address'},
            status=status.HTTP_200_OK,
        )

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def update_user(self, request):
        # get user to edit
        edit_user = User.objects.get(email=request.data.get('email'))
        # get serializer class
        serializer_class = self.get_serializer_class()
        # check if new data is valid
        serializer = serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        # update user
        updated_user = serializer.update(
            user=edit_user,
            data=request.data,
        )
        return Response(UserSerializer(updated_user).data, status=status.HTTP_200_OK)

    @auth_required(as_admin=True)
    @action(detail=False, methods=['post'])
    def deactivate_user(self, request):
        # get user to delete
        deactivate_user = User.objects.get(email=request.data.get('email'))
        # deactivate user
        deactivate_user.is_active = False
        deactivate_user.save()
        return Response(
            {'message': 'The user was deactivated'},
            status=status.HTTP_200_OK,
        )

    @auth_required(as_admin=True)
    @method_decorator(never_cache)
    def list(self, request):
        serializer_class = self.get_serializer_class()
        # return all Users info
        return Response(
            serializer_class(User.objects.all(), many=True).data,
            status=status.HTTP_200_OK,
        )
