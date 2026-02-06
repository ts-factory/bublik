# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.data.serializers import ConfigSerializer
from bublik.interfaces.api_v2_docs.config.serializers import (
    AllVersionsResponseSerializer,
    AvailableTypesNamesResponseSerializer,
    ConfigListResponseSerializer,
    ConfigPartialUpdateRequestSerializer,
    ErrorResponseSerializer,
)


config_viewset_schema = extend_schema_view(
    retrieve=extend_schema(
        summary='Retrieves configuration by ID',
        description='''
        Returns full information about a specific configuration by its ID.
        ''',
        responses={
            200: OpenApiResponse(
                response=ConfigSerializer,
                description='Configuration retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Configuration not found',
            ),
        },
        tags=['Configuration'],
    ),
    update=extend_schema(
        summary='Full updates configuration',
        description='''
        Fully updates a configuration. Requires administrator's role.
        ''',
        request=ConfigSerializer,
        responses={
            200: OpenApiResponse(response=ConfigSerializer),
            400: OpenApiResponse(response=ErrorResponseSerializer),
            403: OpenApiResponse(response=ErrorResponseSerializer),
            404: OpenApiResponse(response=ErrorResponseSerializer),
        },
        tags=['Configuration'],
    ),
    list=extend_schema(
        summary='List of configurations',
        description='''
        Returns a list of configurations.
        Of all configurations having the same project, type and name:
        - If there are active ones, returns active ones
        - If there are none, returns the latest ones
        ''',
        responses={
            200: OpenApiResponse(
                response=ConfigListResponseSerializer,
                description='The list of configurations was successfully received',
            ),
        },
        tags=['Configuration'],
    ),
    create=extend_schema(
        summary='Creates new configuration',
        description='''
        Creates new configuration. Requires administrator's role.
        ''',
        request=ConfigSerializer,
        responses={
            201: OpenApiResponse(
                response=ConfigSerializer,
                description='Configuration created',
            ),
            400: OpenApiResponse(response=ErrorResponseSerializer),
            403: OpenApiResponse(response=ErrorResponseSerializer),
        },
        tags=['Configuration'],
    ),
    partial_update=extend_schema(
        summary='Partial update configuration',
        description='''
        Updates a configuration or creates a new version.
        Requires administrator's role.
        ''',
        request=ConfigPartialUpdateRequestSerializer,
        responses={
            200: OpenApiResponse(response=ConfigSerializer, description='Updated successfully'),
            201: OpenApiResponse(response=ConfigSerializer, description='New version created'),
            400: OpenApiResponse(response=ErrorResponseSerializer),
            403: OpenApiResponse(response=ErrorResponseSerializer),
        },
        tags=['Configuration'],
    ),
    destroy=extend_schema(
        summary='Deletes configuration',
        description='''
        Deletes a configuration. Requires administrator's role.
        ''',
        responses={
            204: OpenApiResponse(description='Deleted successfully'),
            403: OpenApiResponse(response=ErrorResponseSerializer),
            404: OpenApiResponse(response=ErrorResponseSerializer),
        },
        tags=['Configuration'],
    ),
    get_schema=extend_schema(
        summary='Retrieves configuration schema',
        description='''
        Retrieves the JSON schema by passed config type and name.
        ''',
        parameters=[
            OpenApiParameter(
                name='type',
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
            ),
            OpenApiParameter(
                name='name',
                type=str,
                location=OpenApiParameter.QUERY,
                required=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description='JSON schema for the requested configuration',
                examples=[
                    OpenApiExample(
                        name='Generic JSON Schema',
                        summary='Structure of the returned JSON Schema',
                        value={
                            '$schema': 'http://json-schema.org/draft-07/schema#',
                            'type': 'object',
                            'description': 'Description of the requested configuration schema',
                            'properties': {
                                'field_name_1': {
                                    'description': 'Description of the first setting',
                                    'type': 'string',
                                    'default': 'some_value',
                                },
                                'field_name_2': {
                                    'description': 'Description of the second setting',
                                    'type': 'integer',
                                    'minimum': 0,
                                },
                                'nested_object': {
                                    'type': 'object',
                                    'properties': {'sub_field': {'type': 'boolean'}},
                                },
                            },
                            'required': ['field_name_1'],
                            'additionalProperties': False,
                        },
                    ),
                ],
            ),
            422: OpenApiResponse(response=ErrorResponseSerializer),
        },
        tags=['Configuration'],
    ),
    all_versions=extend_schema(
        summary='All versions of configuration',
        description='''
        Retrieves all versions of passed configurations.
        ''',
        responses={
            200: OpenApiResponse(response=AllVersionsResponseSerializer),
            404: OpenApiResponse(response=ErrorResponseSerializer),
        },
        tags=['Configuration'],
    ),
    available_types_names=extend_schema(
        summary='Available configuration types and names',
        description='''
        Returns a reference list of all configuration types and names.
        ''',
        responses={
            200: OpenApiResponse(response=AvailableTypesNamesResponseSerializer),
        },
        tags=['Configuration'],
    ),
)
