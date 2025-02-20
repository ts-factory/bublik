# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from urllib.parse import urljoin

from django.conf import settings
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from bublik.core.config.services import ConfigServices
from bublik.core.shortcuts import build_absolute_uri
from bublik.data.models import GlobalConfigs


def send_importruns_failed_mail(
    requesting_host,
    item_id=None,
    source=None,
    add_to_message=None,
):
    '''
    Sends mails to per-project watchers and Bublik admins.

    Parameter @requesting_host is a Bublik host where importruns failed.
    Parameter @item_id can represent either run id or async task id of importruns.
    Parameter @source is a link through a run was imported.
    Parameter @add_to_message is a string added to the default message.
    '''

    email_from = getattr(settings, 'EMAIL_FROM', None)
    recipients = ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        'EMAIL_PROJECT_WATCHERS',
        default=[],
    ) + getattr(
        settings,
        'EMAIL_ADMINS',
        [],
    )

    if not recipients or not email_from:
        return None

    if not item_id:
        log_url = None
    elif isinstance(item_id, int):
        log_url = urljoin(requesting_host, f'importruns/logs/?run={item_id}')
    else:
        log_url = urljoin(requesting_host, f'importlog/{item_id}')

    subject = 'Importruns failed'

    message_lines = [
        f'Import source: {source}',
        f'Error details: {log_url}',
    ]

    if add_to_message and isinstance(add_to_message, str):
        message_lines.append('\n' + add_to_message)

    message = '\n'.join(message_lines)

    return send_mail(subject, message, email_from, recipients, fail_silently=False)


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        return str(user.is_active) + str(user.pk) + str(timestamp)


def send_verification_link_mail(request, user):
    '''
    Sends an email to the user with a link that activates his account when clicked.
    '''
    from_email = getattr(settings, 'EMAIL_FROM', None)

    email_verification_token = EmailVerificationTokenGenerator()
    user_id_b64 = urlsafe_base64_encode(force_bytes(user.pk))
    token = email_verification_token.make_token(user)

    # construct the verification link URL
    endpoint = f'v2/auth/register/activate/{user_id_b64}/{token}/'
    verify_email_link = build_absolute_uri(request, endpoint)

    send_mail(
        subject='Verify email',
        message=f'Click the following link to verify your email: {verify_email_link}',
        from_email=from_email,
        recipient_list=[user.email],
    )
