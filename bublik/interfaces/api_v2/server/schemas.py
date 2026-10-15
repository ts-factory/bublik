# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view

from bublik.interfaces.api_v2.server.serializers import (
    FeaturesResponse,
    RepoRevisionsSerializer,
    TabTitlePrefixResponseSerializer,
)


SERVER_TAG = 'Server'


server_viewset_schema = extend_schema_view(
    version=extend_schema(
        summary='Retrieve repository revisions',
        description="""
        Get server version information.

        Returns repository revision details: repository URL, branch,
        tag, and the latest commit (date, revision hash, summary).
        """,
        responses={
            200: OpenApiResponse(
                response=RepoRevisionsSerializer,
                description='Revisions were successfully retrieved',
            ),
        },
        tags=[SERVER_TAG],
    ),
    tab_title_prefix=extend_schema(
        summary='Retrieve tab_title_prefix for config',
        description="""
        Get the tab title prefix for a project.

        Returns the prefix configured for the given `project_id`
        in the per-configuration settings.
        """,
        responses={
            200: OpenApiResponse(
                response=TabTitlePrefixResponseSerializer,
                description='tab_title_prefix was successfully retrieved',
            ),
        },
        tags=[SERVER_TAG],
    ),
    features=extend_schema(
        summary='Retrieve settings parameters for analytics and chat',
        description="""
        Get the list of enabled server features.

        Returns flags indicating whether analytics and AI chat
        are currently enabled on the server.
        """,
        responses={
            200: OpenApiResponse(
                response=FeaturesResponse,
                description='Settings parameters were successfully retrieved',
            ),
        },
        tags=[SERVER_TAG],
    ),
)
