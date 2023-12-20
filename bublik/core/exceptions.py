# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import traceback

from rest_framework.response import Response
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    traceback.print_exc()

    data = {
        'type': '',
        'message': '',
    }
    data['type'] = type(exc).__name__
    if exc.args:
        data['message'] = exc.args[0]

    # Call REST framework's default exception handler first,
    # to get the standard error response.
    response = exception_handler(exc, context)

    if response is not None:
        response.data = data
    else:
        response = Response(data, status=500)

    return response
