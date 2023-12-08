# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.contrib.auth import authenticate
from django.core.exceptions import ObjectDoesNotExist
from django.utils.http import urlsafe_base64_decode
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.backends import TokenBackend
from rest_framework_simplejwt.exceptions import TokenBackendError, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from bublik.core.mail import EmailVerificationTokenGenerator, send_verification_link_mail
from bublik.data.models import User
from bublik.data.serializers import (
    RegisterSerializer,
    TokenPairSerializer,
    UserSerializer,
)
from bublik.settings import SIMPLE_JWT


__all__ = [
    'RegisterView',
    'LogInView',
    'ProfileView',
    'RefreshTokenView',
    'LogOutView',
]


def get_user_info_from_access_token(access_token):
    token_backend = TokenBackend(
        algorithm=SIMPLE_JWT['ALGORITHM'],
        signing_key=SIMPLE_JWT['SIGNING_KEY'],
    )
    return token_backend.decode(access_token, verify=True)


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

    def create(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.validate(request.data)
        user = serializer.create(request.data)
        send_verification_link_mail(user)
        return Response(
            'A verification link has been sent to your email address',
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
                'The email is verified. You are registered.',
                status=status.HTTP_200_OK,
            )
        return Response('Invalid email verification link', status=status.HTTP_401_UNAUTHORIZED)


class LogInView(TokenObtainPairView):
    serializer_class = TokenPairSerializer

    def post(self, request):
        # get email and password from request
        email = request.data.get('email')
        password = request.data.get('password')

        # check if email and password are provided
        if not email or not password:
            return Response(
                {'detail': 'Please provide both email and password'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # authenticate user
        user = authenticate(email=email, password=password)

        # check if user is valid
        if not user:
            return Response(
                {'message': 'Invalid credentials'},
                status=status.HTTP_401_UNAUTHORIZED,
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


class ProfileView(APIView):
    serializer_class = UserSerializer

    def get(self, request):
        # get access token from cookies
        access_token = request.COOKIES.get('access_token')
        try:
            # get user info using access token
            user_info = get_user_info_from_access_token(access_token)
            # get user object
            user = User.objects.get(pk=user_info['user_id'])
            return Response(self.serializer_class(user).data, status=status.HTTP_200_OK)
        except TokenBackendError:
            return Response(
                {'message': 'Not Authenticated'},
                status=status.HTTP_401_UNAUTHORIZED,
            )


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
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            access_token = refresh_token.access_token

            # get user info and User object
            user_info = get_user_info_from_access_token(str(access_token))
            user = User.objects.get(pk=user_info['user_id'])

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
                status=status.HTTP_401_UNAUTHORIZED,
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
                    status=status.HTTP_401_UNAUTHORIZED,
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
                status=status.HTTP_401_UNAUTHORIZED,
            )
