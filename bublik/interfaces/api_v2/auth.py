# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.contrib.auth import authenticate
from django.core.exceptions import ObjectDoesNotExist
from django.utils.http import urlsafe_base64_decode
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from bublik.core.mail import EmailVerificationTokenGenerator, send_verification_link_mail
from bublik.data.models import User
from bublik.data.serializers import (
    RegisterSerializer,
    TokenPairSerializer,
    UserSerializer,
)


__all__ = [
    'RegisterView',
    'LogInView',
]


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
