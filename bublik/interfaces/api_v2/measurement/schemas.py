# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.measurement.serializers import (
    MeasurementByResultSerializer,
    MeasurementChartSerializer,
    MeasurementListResponseSerializer,
    MeasurementRequestBodySerializer,
)


MEASUREMENT_TAG = 'Measurements'


measurement_viewset_schema = extend_schema_view(
    list=extend_schema(
        summary='List measurements',
        description="""
        Return a list of available measurements,
        each represented by its defining set of metadata.
        """,
        responses={
            200: OpenApiResponse(
                response=MeasurementListResponseSerializer(many=True),
                description='Measurements were successfully retrieved',
            )
        },
        tags=[MEASUREMENT_TAG],
    ),
    trend_charts=extend_schema(
        summary='Get measurement trend charts',
        description="""
        Build and return measurement trend charts for the specified
        test result IDs.
        """,
        request=MeasurementRequestBodySerializer,
        responses={
            200: OpenApiResponse(
                response=MeasurementChartSerializer(many=True),
                description='Measurement trend charts were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result IDs were not provided',
            ),
        },
        tags=[MEASUREMENT_TAG],
    ),
    by_result_ids=extend_schema(
        summary='Get measurements by result IDs',
        description="""
        Return measurement data, test parameters, and chart series
        for the specified test result IDs.
        """,
        request=MeasurementRequestBodySerializer,
        responses={
            200: OpenApiResponse(
                response=MeasurementByResultSerializer(many=True),
                description='Measurement data for result IDs were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result IDs were not provided',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='One of the specified results was not found',
            ),
        },
        tags=[MEASUREMENT_TAG],
    ),
    retrieve=extend_schema(
        summary='Get measurement',
        description="""
        Return the set of metadata that describes a measurement, identified by its ID.
        """,
        responses={
            200: OpenApiResponse(
                response=MeasurementListResponseSerializer,
                description='Measurement details were successfully retrieved',
            ),
            400: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Measurement ID was not provided or is invalid',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Measurement was not found',
            ),
        },
        tags=[MEASUREMENT_TAG],
    ),
)
