# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Run lifecycle and live Server-Sent-Events delivery for the chat endpoint.

A chat run executes in a background task decoupled from the HTTP request that
started it. Redis bridges that task to the original POST response only; a
reloaded page does not replay the live event stream and instead reads the
persisted thread.

The thread is persisted however the run ends. On a clean finish that is
Pydantic AI's ``on_complete`` result; on cancellation or failure it is the
history accumulated from the native event stream (:class:`~bublik.ai.transcript.PartialRun`),
so an interrupted turn -- including tool calls whose side effects already
happened -- survives the reload instead of vanishing.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import logging
from typing import TYPE_CHECKING

from ag_ui.core import RunErrorEvent
from ag_ui.encoder import EventEncoder

from bublik.ai import run_store
from bublik.ai.transcript import PartialRun, persist_messages


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from typing import Any

    from pydantic_ai import Agent
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.ui.ag_ui import AGUIAdapter

    from bublik.ai.types import AiChatDeps


@dataclass(frozen=True)
class RunOptions:
    """Per-run extras passed through to ``adapter.run_stream``.

    ``capabilities`` carries the run's history compactor and ``on_complete``
    its usage reporter (see :mod:`bublik.ai.compaction`); both are per-run
    state, which is exactly why they ride the run call rather than the
    lru-cached agent. ``model_settings`` is the same story: it carries the
    provider headers resolved for this conversation, which vary per thread and
    so must not be baked into the shared agent. Run-level settings are merged
    over the agent's, so the agent's own settings survive.
    """

    capabilities: Sequence[AbstractCapability] = field(default_factory=tuple)
    on_complete: Any = None
    model_settings: Any = None


logger = logging.getLogger(__name__)

# How long to block on each Redis read while tailing a live run's events.
_STREAM_BLOCK_MS = 15000
# How often a live run polls Redis for its own cancellation flag.
_CANCEL_POLL_S = 0.5

# Strong references to in-flight background runs so the event loop does not
# garbage-collect them while they stream (they are not awaited by any request).
_background_runs: set[asyncio.Task] = set()


async def _buffer_stream(
    adapter: AGUIAdapter,
    run_id: str,
    deps: AiChatDeps,
    options: RunOptions,
    partial: PartialRun,
) -> None:
    """Encode the agent's AG-UI events and append each one to the run's buffer.

    ``adapter.run_stream`` is exactly ``transform_stream(run_stream_native(...))``,
    so the two halves are spelled out here to tap the native events on their way
    through. That feeds ``partial`` with the history :func:`produce_run` needs
    when the run does not reach ``on_complete``.
    """

    async def on_complete(result):
        # The browser may have disconnected, so persist before emitting the
        # terminal event that makes reloads stop showing the background state.
        await persist_messages(
            deps.thread_id,
            [*adapter.messages, *result.new_messages()],
        )
        if options.on_complete is not None:
            async for event in options.on_complete(result):
                yield event

    async def tapped():
        native = adapter.run_stream_native(
            deps=deps,
            capabilities=options.capabilities or None,
            model_settings=options.model_settings,
        )
        async for event in native:
            partial.add(event)
            yield event

    stream = adapter.transform_stream(tapped(), on_complete=on_complete)
    async for sse in adapter.encode_stream(stream):
        await run_store.append_event(run_id, sse, deps.thread_id)


async def _heartbeat(run_id: str, thread_id: str) -> None:
    """Keep a live run's Redis keys from expiring while it is still running.

    Events refresh the keys as they are appended, but a run can legitimately go
    quiet for longer than the TTL (a slow provider, a long-running tool), and
    losing the thread pointer mid-run breaks cancellation and the stream tail.
    """
    while True:
        await asyncio.sleep(run_store.HEARTBEAT_INTERVAL_S)
        await run_store.touch_run(run_id, thread_id)


async def _watch_cancel(run_id: str) -> None:
    """Return once cancellation is requested for ``run_id``.

    Runs as its own coroutine alongside :func:`_buffer_stream`, so it detects the
    Redis flag even while the stream loop is wedged inside a hung ``async for``
    (a stalled provider or tool that never yields and never raises) -- which is
    exactly the "stuck streaming" case a manual interrupt has to break.
    """
    while not await run_store.is_cancel_requested(run_id):
        await asyncio.sleep(_CANCEL_POLL_S)


async def produce_run(
    adapter: AGUIAdapter,
    agent: Agent,
    run_id: str,
    deps: AiChatDeps,
    options: RunOptions,
) -> None:
    """Run the agent and buffer every encoded AG-UI event into Redis.

    Runs in a background task. Agent-level errors are surfaced to subscribers as
    AG-UI error events by ``run_stream`` itself; we only need to make sure the
    terminal sentinel is always written so subscribers stop tailing.

    The buffering loop is raced against a cancel-watcher so a user can interrupt a
    run (including one stuck on a provider/tool that never yields): when the
    watcher wins we cancel the stream and emit our own ``RunErrorEvent``, because a
    cancelled task raises ``asyncio.CancelledError`` (a ``BaseException``) which
    pydantic-ai's stream does NOT convert into a terminal event -- without it the
    client would never leave the streaming state.

    The stream runs inside ``async with agent`` so any remote MCP toolsets the
    agent was built with (see :func:`bublik.ai.agent.build_agent`) are connected
    for the run and torn down afterwards. With no MCP servers configured this is
    a cheap no-op, so it is unconditional.
    """
    status = 'finished'
    cancelled = False
    partial = PartialRun()
    heartbeat_task = asyncio.ensure_future(_heartbeat(run_id, deps.thread_id))
    try:
        async with agent:
            stream_task = asyncio.ensure_future(
                _buffer_stream(adapter, run_id, deps, options, partial)
            )
            watch_task = asyncio.ensure_future(_watch_cancel(run_id))
            done, _pending = await asyncio.wait(
                {stream_task, watch_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if watch_task in done and stream_task not in done:
                # Cancellation requested while the stream was still running.
                cancelled = True
                stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stream_task
            else:
                # Stream finished (or raised) first; stop watching and re-await it
                # so any real failure propagates to the handler below.
                watch_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watch_task
                await stream_task
    except asyncio.CancelledError:
        status = 'error'
        raise
    except Exception:
        logger.exception('chat run %s failed', run_id)
        status = 'error'
    finally:
        heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        if cancelled:
            status = 'cancelled'
            # A cancelled task bypasses pydantic-ai's error handling, so emit the
            # terminal event ourselves; finish_run then writes the EOT sentinel.
            error = EventEncoder().encode(
                RunErrorEvent(message='Run cancelled.', code='cancelled')
            )
            await run_store.append_event(run_id, error, deps.thread_id)
        if status != 'finished':
            # on_complete never ran, so nothing has been written for this turn.
            # Persist what the model produced before the interruption; failing
            # to do so must not stop finish_run from releasing the thread.
            try:
                await persist_messages(
                    deps.thread_id,
                    [*adapter.messages, *partial.snapshot()],
                )
            except Exception:
                logger.exception('failed to persist partial output for run %s', run_id)
        await run_store.finish_run(run_id, status)


def spawn_run(
    adapter: AGUIAdapter,
    agent: Agent,
    run_id: str,
    deps: AiChatDeps,
    options: RunOptions,
) -> None:
    """Start :func:`produce_run` as a tracked background task.

    A strong reference is held in ``_background_runs`` until the task finishes so
    the event loop does not garbage-collect the run mid-stream (nothing awaits
    it).
    """
    task = asyncio.create_task(produce_run(adapter, agent, run_id, deps, options))
    _background_runs.add(task)
    task.add_done_callback(_background_runs.discard)


async def stream_run_events(run_id: str) -> AsyncIterator[str]:
    """Yield the events for the POST request that created ``run_id``.

    The stream starts at the beginning to cover events emitted between spawning
    the background task and opening the HTTP response body. This is not a
    reconnect API: the run id is never exposed as a later subscription target.
    """
    last_id = '0'
    while True:
        entries = await run_store.read(run_id, last_id, _STREAM_BLOCK_MS)
        if not entries:
            # Timed out. Stop if the run is no longer running (and somehow never
            # wrote a sentinel); otherwise send a keep-alive comment and wait.
            if await run_store.run_status(run_id) != 'running':
                return
            yield ': keep-alive\n\n'
            continue
        for entry_id, fields in entries:
            last_id = entry_id
            if run_store.EOT_FIELD in fields:
                return
            data = fields.get(run_store.DATA_FIELD)
            if data is not None:
                yield data
