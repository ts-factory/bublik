# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import io

from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from bublik.core.parsers import JSONParser


def parse(body):
    return JSONParser().parse(io.BytesIO(body.encode()))


class EchoView(APIView):
    """A view with the parsers of the API's settings, giving back the body."""

    authentication_classes = ()
    permission_classes = ()

    def post(self, request):
        return Response(request.data)


class JSONParserTest(SimpleTestCase):
    def test_object_is_parsed(self):
        assert parse('{"a": "1", "b": {"c": [1, {"d": 2}]}}') == {
            'a': '1',
            'b': {'c': [1, {'d': 2}]},
        }

    def test_repeated_key_is_rejected(self):
        with self.assertRaisesMessage(ParseError, "Repeated key 'a'"):
            parse('{"a": "1", "a": "2"}')

    def test_repeated_key_with_the_same_value_is_rejected(self):
        with self.assertRaisesMessage(ParseError, "Repeated key 'a'"):
            parse('{"a": "1", "a": "1"}')

    def test_repeated_nested_key_is_rejected(self):
        with self.assertRaisesMessage(ParseError, "Repeated key 'b'"):
            parse('{"a": [{"b": 1, "b": 2}]}')

    def test_same_key_in_different_objects_is_fine(self):
        assert parse('[{"a": 1}, {"a": 2}]') == [{'a': 1}, {'a': 2}]

    def test_non_compliant_float_is_still_rejected(self):
        with self.assertRaises(ParseError):
            parse('{"a": NaN}')


class DefaultParserTest(SimpleTestCase):
    def post(self, body):
        request = APIRequestFactory().post('/', body, content_type='application/json')
        return EchoView.as_view()(request)

    def test_api_rejects_repeated_key(self):
        response = self.post('{"a": "1", "a": "2"}')
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data == {'messages': ["JSON parse error - Repeated key 'a'"]}

    def test_api_parses_object(self):
        response = self.post('{"a": "1"}')
        assert response.status_code == status.HTTP_200_OK
        assert response.data == {'a': '1'}
