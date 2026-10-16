# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)

from bublik.interfaces.api_v2.errors.serializers import ErrorResponseSerializer
from bublik.interfaces.api_v2.tree.serializers import (
    PathTreeSerializer,
    TreeResponseSerializer,
)


TREE_TAG = 'Tree'
TREE_RESULT_PARAMETER = OpenApiParameter(
    name='id',
    type=OpenApiTypes.INT,
    location=OpenApiParameter.PATH,
    required=True,
    description='Test run or test result ID',
)


tree_viewset_schema = extend_schema_view(
    retrieve=extend_schema(
        summary='Get result tree',
        description="""
        Returns a flat mapping of result IDs to tree nodes, together with the
        main package ID. Each node includes its start time, name, entity type,
        error and skipped flags, and child IDs when children exist.
        For a test run, returns the complete tree. For another result, returns
        its subtree and uses the requested result ID as main_package.
        """,
        parameters=[TREE_RESULT_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=TreeResponseSerializer,
                description='Result tree was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[TREE_TAG],
    ),
    path=extend_schema(
        summary='Get path to result',
        description="""
        Returns an ordered list of ancestor node IDs followed by the requested
        result ID. When the result has no main package, the list contains only
        the requested result ID.
        """,
        parameters=[TREE_RESULT_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=PathTreeSerializer,
                description='Result path was successfully retrieved',
            ),
            404: OpenApiResponse(
                response=ErrorResponseSerializer,
                description='Result was not found',
            ),
        },
        tags=[TREE_TAG],
    ),
)
