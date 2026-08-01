# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Automatic model-side history compaction and context-usage reporting.

The chat backend persists the full ``UIMessage[]`` transcript and the client
resends it on every turn, so long threads eventually exceed the model's context
window. This module keeps the *stored*
conversation intact and compacts only what is sent to the model: when the
estimated context occupancy crosses a configured fraction of the model's
context window (``AiConfig.compaction``), the older turns are summarized by
a tool-less agent and replaced with a single summary message.

Both halves ride the existing per-run machinery (no agent-cache changes):

* :class:`Compactor` is a per-run ``ProcessHistory`` history processor
  (``adapter.run_stream(capabilities=[ProcessHistory(compactor)], ...)``).
  It runs before every model request; applying a cached summary is cheap,
  and fresh summarization happens at most once per run. The summary and the
  count of leading messages it covers are cached in
  ``ChatThread.context_state`` so later turns (which resend the full
  history) reuse it instead of re-summarizing.
* :func:`make_usage_reporter` builds the ``on_complete`` callback for
  ``adapter.run_stream``: it reads the run's final real token usage,
  persists it to ``ChatThread.context_state`` (the *next* run's threshold
  input, and the thread-detail endpoint's ``context_usage`` seed) and yields
  an AG-UI ``CustomEvent`` so the UI's context meter updates live.

Models without a known context window are never compacted and report no
usage limit; the whole feature is inert for them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ag_ui.core import CustomEvent
from ag_ui.encoder import EventEncoder
from asgiref.sync import sync_to_async
from django.utils import timezone
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

# Runtime imports (not TYPE_CHECKING): pydantic-ai's history-processor
# dispatch resolves `Compactor.__call__`'s first-parameter annotation at
# runtime (`takes_run_context`) to decide whether to pass the RunContext, so
# `RunContext`/`ChatDeps` must be importable when the hint is evaluated.
from pydantic_ai.tools import RunContext  # noqa: TC002

from bublik.ai import run_store
from bublik.ai.prompts import COMPACTION_PROMPT
from bublik.ai.types import ChatDeps, CompactionConfig  # noqa: TC001
from bublik.data.models import ChatThread


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic_ai.models import Model
    from pydantic_ai.run import AgentRunResult


logger = logging.getLogger(__name__)

# AG-UI CustomEvent names understood by the chat UI.
CONTEXT_USAGE_EVENT = 'bublik.chat.context_usage'
COMPACTED_EVENT = 'bublik.chat.compacted'

# The compacted history opens with this marker so the model knows the summary
# stands for real earlier turns, not user input.
_SUMMARY_PREFIX = 'Summary of the earlier conversation (older messages were compacted):\n\n'

# Crude chars-per-token ratio for the estimate fallback (no tokenizer dep;
# real usage from the previous run takes precedence whenever it exists).
_CHARS_PER_TOKEN = 4
# Rough per-message protocol overhead, in tokens.
_MESSAGE_OVERHEAD_TOKENS = 4
# Tool results dominate transcript size; cap each one in the summarizer input.
_TRANSCRIPT_TOOL_RESULT_CHARS = 4000


def _read_state(thread_id: str) -> dict:
    """The thread's ``context_state`` blob, or ``{}`` (row may not exist yet)."""
    state = (
        ChatThread.objects.filter(id=thread_id).values_list('context_state', flat=True).first()
    )
    return state or {}


def _write_state(thread_id: str, updates: dict) -> None:
    """Merge ``updates`` into the thread's ``context_state``.

    The chat route creates the thread before a run starts. Keep this guard for
    cleanup races where a thread is deleted while a background task finishes.
    """
    thread = ChatThread.objects.filter(id=thread_id).first()
    if thread is None:
        return
    state = thread.context_state or {}
    state.update(updates)
    thread.context_state = state
    # Deliberately not bumping `updated`: context bookkeeping is not a
    # user-visible change and must not reorder the sidebar.
    thread.save(update_fields=['context_state'])


def _part_texts(part: object) -> list[str]:
    """Text fragments of one message part, for token estimation."""
    content = getattr(part, 'content', None)
    if isinstance(content, str):
        return [content]
    if isinstance(content, (list, tuple)):
        return [item for item in content if isinstance(item, str)]
    if content is not None:
        return [str(content)]
    if isinstance(part, ToolCallPart):
        return [part.tool_name, part.args_as_json_str()]
    return []


def estimate_tokens(messages: list[ModelMessage]) -> int:
    """Heuristic token estimate (chars/4) of a message list.

    Fallback for threads with no recorded real usage yet -- most importantly
    a giant first-turn paste, which must be able to trigger compaction before
    any response ever succeeded.
    """
    chars = 0
    count = 0
    for message in messages:
        count += 1
        for part in message.parts:
            chars += sum(len(text) for text in _part_texts(part))
    return chars // _CHARS_PER_TOKEN + count * _MESSAGE_OVERHEAD_TOKENS


def _is_user_request(message: ModelMessage) -> bool:
    """Whether the message is a ``ModelRequest`` carrying real user input."""
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def _split_point(messages: list[ModelMessage], keep_recent: int) -> int:
    """Index where the kept-verbatim window starts (0 = nothing to compact).

    Aims to keep the last ``keep_recent`` messages, then moves the boundary
    further back until the kept window starts at a user request -- so a tool
    call is never separated from its return and the model always sees the
    user turn its recent context belongs to.
    """
    idx = max(len(messages) - keep_recent, 0)
    while idx > 0 and not _is_user_request(messages[idx]):
        idx -= 1
    return idx


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f'… [{len(text) - limit} chars truncated]'


def _transcript(messages: list[ModelMessage]) -> str:
    """Render messages to the plain-text transcript the summarizer reads.

    Thinking is dropped (internal), tool results are truncated (they dominate
    size and the summary only needs their gist).
    """
    lines: list[str] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                for text in _part_texts(part):
                    lines.append(f'User: {text}')
            elif isinstance(part, TextPart):
                lines.append(f'Assistant: {part.content}')
            elif isinstance(part, ToolCallPart):
                args = _truncate(part.args_as_json_str(), 500)
                lines.append(f'Assistant called tool {part.tool_name}({args})')
            elif isinstance(part, ToolReturnPart):
                content = _truncate(str(part.content), _TRANSCRIPT_TOOL_RESULT_CHARS)
                lines.append(f'Tool {part.tool_name} returned: {content}')
            elif isinstance(part, RetryPromptPart):
                lines.append(f'Tool error: {_truncate(str(part.content), 500)}')
    return '\n\n'.join(lines)


def _summary_message(summary: str) -> ModelRequest:
    """The ``ModelMessage`` that stands in for the compacted turns.

    A plain user-role request: every provider accepts it anywhere in the
    history, unlike system parts (stripped by the adapter's sanitizer) or
    fabricated assistant turns.
    """
    return ModelRequest(parts=[UserPromptPart(content=_SUMMARY_PREFIX + summary)])


class Compactor:
    """Per-run history processor implementing threshold-triggered compaction.

    One instance per run (constructed in ``app._run_chat``); all thread state
    lives in ``ChatThread.context_state``, so the lru-cached agent stays
    shareable. Called before every model request of the run: applies the
    cached summary cheaply, summarizes afresh at most once.
    """

    def __init__(
        self,
        *,
        config: CompactionConfig,
        context_limit: int | None,
        summarizer_model: Model | None,
    ) -> None:
        self._config = config
        self._context_limit = context_limit
        self._summarizer_model = summarizer_model
        self._state: dict | None = None
        self._compacted_this_run = False

    @property
    def _active(self) -> bool:
        return (
            self._config.enabled
            and self._context_limit is not None
            and self._summarizer_model is not None
        )

    async def __call__(
        self,
        ctx: RunContext[ChatDeps],
        messages: list[ModelMessage],
    ) -> list[ModelMessage]:
        if not self._active:
            return messages
        try:
            return await self._process(ctx, messages)
        except Exception:
            # Compaction is an optimization; a failure (summarizer error, DB
            # hiccup) must degrade to the uncompacted conversation, not kill
            # the user's turn.
            logger.exception(
                'history compaction failed for thread %s; continuing uncompacted',
                ctx.deps.thread_id,
            )
            return messages

    async def _process(
        self,
        ctx: RunContext[ChatDeps],
        messages: list[ModelMessage],
    ) -> list[ModelMessage]:
        if self._state is None:
            self._state = await sync_to_async(_read_state)(ctx.deps.thread_id)

        working, covered = self._apply_cached_summary(messages)
        if self._compacted_this_run:
            # Later model requests of the same run (tool loops) reuse the
            # summary made above; the in-run growth is bounded by the run.
            return working

        # Real usage from the previous run is authoritative, but the estimate
        # still guards the gap it cannot see: content added since that run
        # (most importantly a giant paste, possibly on the very first turn).
        occupancy = max(
            self._state.get('context_tokens') or 0,
            estimate_tokens(working),
        )
        if occupancy < self._config.threshold * self._context_limit:
            return working

        return await self._compact(ctx, working, covered)

    def _apply_cached_summary(
        self,
        messages: list[ModelMessage],
    ) -> tuple[list[ModelMessage], int]:
        """Replace the summarized leading messages with the cached summary.

        Returns the working list and how many original messages the applied
        summary covers (0 when no cache applies). A cache covering more
        messages than the client sent means the history was truncated
        (retry/edit); it is dropped as stale.
        """
        summary = self._state.get('summary')
        covered = self._state.get('covered_count') or 0
        if not summary or covered <= 0:
            return messages, 0
        if covered > len(messages):
            self._state.pop('summary', None)
            self._state.pop('covered_count', None)
            return messages, 0
        return [_summary_message(summary), *messages[covered:]], covered

    async def _compact(
        self,
        ctx: RunContext[ChatDeps],
        working: list[ModelMessage],
        covered: int,
    ) -> list[ModelMessage]:
        split = _split_point(working, self._config.keep_recent)
        if split <= 0:
            return working

        transcript = _transcript(working[:split])
        if not transcript:
            return working

        summarizer = Agent(self._summarizer_model, instructions=COMPACTION_PROMPT)
        result = await summarizer.run(transcript)
        summary = result.output

        # `covered_count` is relative to the *original* client history: when a
        # cached summary was applied, working[0] stands for `covered` original
        # messages, so the freshly summarized prefix covers `split - 1` more.
        covered_count = covered + split - 1 if covered else split
        self._state.update(
            {
                'summary': summary,
                'covered_count': covered_count,
                'compacted_at': timezone.now().isoformat(),
            },
        )
        self._compacted_this_run = True
        await sync_to_async(_write_state)(
            ctx.deps.thread_id,
            {
                'summary': summary,
                'covered_count': covered_count,
                'compacted_at': self._state['compacted_at'],
            },
        )

        # Surface the compaction to the client through the run's event buffer.
        # Appending directly interleaves correctly: the buffer is a Redis
        # stream shared with the run's encoded AG-UI events.
        event = EventEncoder().encode(
            CustomEvent(
                name=COMPACTED_EVENT,
                value={'covered_messages': covered_count},
            ),
        )
        await run_store.append_event(ctx.deps.run_id, event)

        logger.info(
            'compacted thread %s: %d messages summarized (context ~%d tokens)',
            ctx.deps.thread_id,
            covered_count,
            self._state.get('context_tokens') or 0,
        )
        return [_summary_message(summary), *working[split:]]


def make_usage_reporter(
    thread_id: str,
    provider_id: str,
    model_id: str,
    context_limit: int | None,
):
    """Build the ``on_complete`` hook reporting the run's final context usage.

    An async generator (the shape ``adapter.run_stream`` accepts): persists
    the last model request's real token usage to ``ChatThread.context_state``
    and yields a ``CustomEvent`` that flows through the normal encode/buffer
    path to the client. Only successful runs get here, so a cancelled or
    failed run leaves the previous good value in place.
    """

    async def on_complete(result: AgentRunResult) -> AsyncIterator[CustomEvent]:
        response = next(
            (m for m in reversed(result.all_messages()) if isinstance(m, ModelResponse)),
            None,
        )
        usage = response.usage if response is not None else None
        if usage is None:
            return
        # Current context occupancy: everything the last request processed
        # (fresh + cached input) plus what it produced.
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
