# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import exception_handler


logger = logging.getLogger(__name__)


def normalize_error_details(error_details):
    if isinstance(error_details, dict):
        if 'detail' in error_details:
            return [error_details['detail']]
        return {
            field: [str(e) for e in (errors if isinstance(errors, (list, tuple)) else [errors])]
            for field, errors in error_details.items()
        }
    return [
        str(ed)
        for ed in (
            error_details if isinstance(error_details, (list, tuple)) else [error_details]
        )
    ]


def custom_exception_handler(exc, context):
    # Call REST framework's default exception handler first,
    # to get the standard error response.
    response = exception_handler(exc, context)

    if response is None or getattr(exc, '__cause__', None):
        logger.error('Exception occurred:', exc_info=exc)

    if response is None:
        msg = 'Unexpected server error. Try refreshing and let us know if it keeps happening.'
        updated_response_data = {'messages': normalize_error_details(msg)}
        return Response(
            updated_response_data,
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    updated_response_data = {'messages': normalize_error_details(response.data)}
    response.data = updated_response_data

    return response


class BublikError(Exception):
    def __init__(self, message='Internal server error'):
        super().__init__(message)
        self.message = message


class BublikServerError(BublikError):
    pass


class BublikAPIError(BublikError, APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = 'Something went wrong, please try again'

    def __init__(self, message=None, status_code=None):
        if message is None:
            message = self.default_detail
        BublikError.__init__(self, message)
        APIException.__init__(self, detail=message)
        if status_code is not None:
            self.status_code = status_code


class BadGatewayError(BublikAPIError):
    status_code = status.HTTP_502_BAD_GATEWAY
    default_detail = 'Bad gateway'


class UnprocessableEntityError(BublikAPIError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    default_detail = 'Unprocessable entity'
