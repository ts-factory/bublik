# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Authentication and authorization for the chat endpoints.

The chat routes (:mod:`bublik.ai.app`, :mod:`bublik.ai.streaming`,
:mod:`bublik.ai.downloads`) resolve the requesting user from the
``access_token`` cookie and gate access to threads and generated files by
ownership. This is the ORM/permission layer for those routes, kept apart from
the route wiring so it can be unit-tested on its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError

from bublik.core.auth import get_user_by_access_token
from bublik.data.models import ChatFile, ChatThread


if TYPE_CHECKING:
    from starlette.requests import Request


async def resolve_user(request: Request):
    """Resolve the requesting user from the ``access_token`` cookie, or ``None``."""
    access_token = request.cookies.get('access_token')
    if not access_token:
        return None
    return await sync_to_async(get_user_by_access_token)(access_token)


@sync_to_async
def _thread_owner_id(thread_id: str) -> int | None:
    """Owner user id of an existing thread, or ``None`` if it does not exist.

    Thread ids are UUIDs; a malformed id matches no thread (treated as missing)
    rather than raising, so a bad value never turns into a 500.
    """
    try:
        return ChatThread.objects.filter(pk=thread_id).values_list('user_id', flat=True).first()
    except (ValidationError, ValueError):
        return None


async def user_may_access_thread(user, thread_id: str) -> bool:
    """A user may access a thread it owns.

    The thread row is created in ``_run_chat`` before the access check, so a
    missing row means the thread belongs to no one (or was deleted) and access is
    denied.
    """
    owner_id = await _thread_owner_id(thread_id)
    return owner_id is not None and owner_id == user.id


@sync_to_async
def get_owned_file(file_id: str, user_id: int) -> ChatFile | None:
    """The user's ChatFile with the given id, or ``None``.

    File ids are UUIDs; a malformed id matches no file (treated as missing)
    rather than raising, mirroring ``_thread_owner_id``.
    """
    try:
        return (
            ChatFile.objects.select_related('thread')
            .filter(pk=file_id, thread__user_id=user_id)
            .first()
        )
    except (ValidationError, ValueError):
        return None
