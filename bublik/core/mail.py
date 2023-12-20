# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from urllib.parse import urljoin

from django.conf import settings
from django.core.mail import send_mail
import per_conf


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
    recipients = getattr(per_conf, 'EMAIL_PROJECT_WATCHERS', []) + getattr(
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
        log_url = urljoin(requesting_host, f'v1/logs/{item_id}')

    subject = 'Importruns failed'

    message_lines = [
        f'Import source: {source}',
        f'Error details: {log_url}',
    ]

    if add_to_message and isinstance(add_to_message, str):
        message_lines.append('\n' + add_to_message)

    message = '\n'.join(message_lines)

    return send_mail(subject, message, email_from, recipients, fail_silently=False)
