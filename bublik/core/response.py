# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework import status
from rest_framework.response import Response


def error_details(msg, view_name):
    return {
        'data': {'detail': msg, 'view': view_name, 'alert_type': 'danger'},
        'template_name': 'bublik/alert.html',
    }


def bad_request(msg, view_name):
    response_data = {'status': status.HTTP_400_BAD_REQUEST}
    response_data.update(error_details(msg, view_name))
    return Response(**response_data)


def internal_error(msg, view_name):
    response_data = {'status': status.HTTP_500_INTERNAL_SERVER_ERROR}
    response_data.update(error_details(msg, view_name))
    return Response(**response_data)
