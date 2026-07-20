# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from typing import ClassVar
import uuid

from django.db import models

from bublik.data.models.user import User


__all__ = [
    'ChatThread',
]


class ChatThread(models.Model):
    """
    A persisted AI chat conversation.

    One row per thread; the whole message list is stored as a JSON blob so it
    is written by the AI run backend as a complete ``UIMessage[]`` transcript.
    Threads are private to the user that created them.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text='Thread identifier (client-supplied).',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='chat_threads',
        help_text='The user who owns the thread.',
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        help_text='Human-readable thread title.',
    )
    is_archived = models.BooleanField(
        default=False,
        help_text='Whether the thread is archived (hidden from the default list).',
    )
    messages = models.JSONField(
        default=list,
        help_text='The conversation messages (UIMessage[]).',
    )
    context_state = models.JSONField(
        default=dict,
        help_text=(
            'Server-managed context bookkeeping: last known context token '
            'occupancy and the cached compaction summary (see bublik.ai.compaction). '
            'Never written by the client.'
        ),
    )
    created = models.DateTimeField(
        auto_now_add=True,
        help_text='Timestamp of the thread creation.',
    )
    updated = models.DateTimeField(
        auto_now=True,
        help_text='Timestamp of the last thread update.',
    )

    class Meta:
        db_table = 'bublik_chat_thread'
        ordering: ClassVar[list] = ['-updated']

    def __repr__(self):
        return (
            f'ChatThread(id={self.id!r}, user={self.user_id!r}, title={self.title!r}, '
            f'is_archived={self.is_archived!r}, updated={self.updated!r})'
        )
