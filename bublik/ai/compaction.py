# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Context-usage reporting for completed AI chat runs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ag_ui.core import CustomEvent
from asgiref.sync import sync_to_async
from pydantic_ai.messages import ModelResponse

from bublik.data.models import ChatThread


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai.run import AgentRunResult


CONTEXT_USAGE_EVENT = 'bublik.chat.context_usage'


def _write_state(thread_id: str, updates: dict) -> None:
    """Merge context bookkeeping into a thread without reordering the sidebar."""
    thread = ChatThread.objects.filter(id=thread_id).first()
    if thread is None:
        return
    state = thread.context_state or {}
    state.update(updates)
    thread.context_state = state
    thread.save(update_fields=['context_state'])


def make_usage_reporter(
    thread_id: str,
    provider_id: str,
    model_id: str,
    context_limit: int | None,
):
    """Build an ``on_complete`` hook that persists and emits context usage."""

    async def on_complete(result: AgentRunResult) -> AsyncIterator[CustomEvent]:
        response = next(
            (
                message
                for message in reversed(result.all_messages())
                if isinstance(message, ModelResponse)
            ),
            None,
        )
        usage = response.usage if response is not None else None
        if usage is None:
            return
        tokens = (
            (usage.input_tokens or 0)
            + (usage.cache_read_tokens or 0)
            + (usage.cache_write_tokens or 0)
            + (usage.output_tokens or 0)
        )
        if tokens <= 0:
            return
        await sync_to_async(_write_state)(
            thread_id,
            {
                'context_tokens': tokens,
                'context_limit': context_limit,
                'provider': provider_id,
                'model': model_id,
            },
        )
        yield CustomEvent(
            name=CONTEXT_USAGE_EVENT,
            value={
                'tokens': tokens,
                'context_limit': context_limit,
                'provider': provider_id,
                'model': model_id,
            },
        )

    return on_complete
