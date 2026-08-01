# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
ASGI config for the bublik project.

It exposes the ASGI callable as a module-level variable named ``application``.

The application routes the AG-UI chat endpoints (served by Pydantic AI) first,
and falls back to the regular Django ASGI application for everything else.
Existing synchronous Django/DRF views keep working: under ASGI they are run in
a threadpool automatically.
"""

import os

from django.core.asgi import get_asgi_application


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')

# Initialise Django before importing anything that touches settings/ORM.
django_application = get_asgi_application()

from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from bublik.ai import build_chat_routes  # noqa: E402


application = Starlette(
    routes=[
        *build_chat_routes(),
        Mount('/', app=django_application),
    ],
)
