# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import typing

from django.conf import settings
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from bublik.core.config.services import ConfigServices
from bublik.core.server import ServerService
from bublik.data.models import GlobalConfigs


__all__ = [
    'ServerViewSet',
]


class ServerViewSet(ViewSet):
    filter_backends: typing.ClassVar['list'] = []

    @action(detail=False, methods=['get'])
    def version(self, request):
        data = ServerService.get_version()
        return Response(data=data)

    @action(detail=False, methods=['get'])
    def tab_title_prefix(self, _request):
        project_id = self.request.query_params.get('project_id')

        return Response(
            {
                'tab_title_prefix': ConfigServices.getattr_from_global(
                    GlobalConfigs.PER_CONF.name,
                    'TAB_TITLE_PREFIX',
                    project_id,
                ),
            },
        )

    @action(detail=False, methods=['get'])
    def features(self, _request):
        return Response(
            {
                'analytics_enabled': settings.ANALYTICS_ENABLED,
                'chat_enabled': settings.AI_CHAT_ENABLED,
                'user_mcp_servers_enabled': _user_mcp_servers_enabled(),
            },
        )


def _user_mcp_servers_enabled() -> bool:
    """Whether the chat is on and the policy allows any host (a broken config allows none)."""
    if not settings.AI_CHAT_ENABLED:
        return False
    from bublik.ai.config import get_ai_config  # noqa: PLC0415

    try:
        return get_ai_config().user_mcp_servers.enabled
    except Exception:  # a broken config must not break the features endpoint
        return False
