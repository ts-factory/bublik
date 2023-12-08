# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.core.exceptions import ObjectDoesNotExist
from django.utils.http import urlsafe_base64_decode
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from bublik.core.mail import EmailVerificationTokenGenerator, send_verification_link_mail
from bublik.data.models import User
from bublik.data.serializers import RegisterSerializer


__all__ = [
    'RegisterView',
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
