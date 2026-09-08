# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Unit tests for the chat run lifecycle (:mod:`bublik.ai.streaming`).

Focused on cancellation and live POST delivery: a run whose event stream is
wedged must still be interruptible, and the original request receives its
events while a reloaded page reads only the final persisted transcript. Backed
by ``fakeredis`` like :mod:`bublik.tests.test_run_store`.
"""

from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase, mock

import fakeredis
import fakeredis.aioredis
from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolResultEvent,
    ToolReturnPart,
)

from bublik.ai import run_store, streaming
from bublik.ai.transcript import serialize_messages
from bublik.ai.types import AiChatDeps


class _StubAgent:
    """Stand-in for the pydantic-ai agent's ``async with`` (MCP) lifecycle."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _StubAdapter:
    """Minimal AGUIAdapter: exposes the two-stage stream the buffering loop drives.

    ``_buffer_stream`` splits ``run_stream`` into ``run_stream_native`` (tapped
    for the partial-history accumulator) and ``transform_stream``, so the stub
    replays ``native_events`` through the former and the caller-supplied SSE
    strings through ``encode_stream``.
    """

    def __init__(self, stream_factory, native_events=()):
        self.run_input = type('RunInput', (), {'messages': []})()
        self.messages = []
        self.model_settings = None
        self._stream_factory = stream_factory
        self._native_events = list(native_events)

    def run_stream_native(self, *, deps=None, capabilities=None, model_settings=None):
        self.model_settings = model_settings

        async def events():
            for event in self._native_events:
                yield event

        return events()

    def transform_stream(self, stream, on_complete=None):
        return stream

    def encode_stream(self, stream):
        async def encoded():
            # Drain the native stream first so the accumulator sees every event,
            # then hand back the SSE chunks the test asked for.
            async for _event in stream:
                pass
            async for sse in self._stream_factory():
                yield sse

        return encoded()


_wedged_started = asyncio.Event()


async def _wedged_stream():
    """Signal that buffering began, then block forever without yielding."""
    _wedged_started.set()
    await asyncio.Event().wait()
    yield ''  # pragma: no cover - never reached


class ProduceRunCancelTest(IsolatedAsyncioTestCase):
    def setUp(self):
        server = fakeredis.FakeServer()
        run_store._aredis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        run_store._sredis = fakeredis.FakeRedis(server=server, decode_responses=True)

    def tearDown(self):
        run_store._aredis = None
        run_store._sredis = None

    async def _drain(self, run_id):
        return await run_store.read(run_id, '0', 1)

    async def test_cancel_interrupts_a_wedged_stream(self):
        started = asyncio.Event()

        async def wedged_stream():
            # Signal that buffering began, then block forever without yielding --
            # the "stuck streaming" case a manual interrupt has to break.
            started.set()
            await asyncio.Event().wait()
            yield ''  # pragma: no cover - never reached

        await run_store.register_run('run1', 'thread1', 7)
        adapter = _StubAdapter(wedged_stream)
        deps = AiChatDeps(thread_id='thread1', user_id=7, run_id='run1')

        task = asyncio.ensure_future(
            streaming.produce_run(adapter, _StubAgent(), 'run1', deps, streaming.RunOptions())
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        await run_store.request_cancel('run1')
        await asyncio.wait_for(task, timeout=5)

        self.assertEqual(await run_store.run_status('run1'), 'cancelled')
        entries = await self._drain('run1')
        # A terminal error event was buffered before the sentinel...
        datas = [f[run_store.DATA_FIELD] for _id, f in entries if run_store.DATA_FIELD in f]
        self.assertTrue(any('cancelled' in d for d in datas))
        # ...and the run was finished with the cancelled sentinel.
        _last_id, last_fields = entries[-1]
        self.assertEqual(last_fields.get(run_store.EOT_FIELD), 'cancelled')

    async def test_normal_stream_finishes(self):
        async def stream():
            yield 'data: a\n\n'
            yield 'data: b\n\n'

        await run_store.register_run('run2', 'thread2', 7)
        adapter = _StubAdapter(stream)
        deps = AiChatDeps(thread_id='thread2', user_id=7, run_id='run2')

        await streaming.produce_run(adapter, _StubAgent(), 'run2', deps, streaming.RunOptions())

        self.assertEqual(await run_store.run_status('run2'), 'finished')
        entries = await self._drain('run2')
        _last_id, last_fields = entries[-1]
        self.assertEqual(last_fields.get(run_store.EOT_FIELD), 'finished')


class ProduceRunPartialPersistTest(IsolatedAsyncioTestCase):
    """A run that never reaches ``on_complete`` must still persist its turn."""

    def setUp(self):
        server = fakeredis.FakeServer()
        run_store._aredis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        run_store._sredis = fakeredis.FakeRedis(server=server, decode_responses=True)

    def tearDown(self):
        run_store._aredis = None
        run_store._sredis = None

    @staticmethod
    def _native_events():
        """A text part, a completed tool call, then the start of a second turn."""
        return [
            PartStartEvent(index=0, part=TextPart(content='Looking')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=' it up')),
            PartStartEvent(
                index=1,
                part=ToolCallPart(tool_name='get_run', args={'id': 1}, tool_call_id='tc1'),
            ),
            ToolResultEvent(
                part=ToolReturnPart(
                    tool_name='get_run', content={'ok': True}, tool_call_id='tc1'
                )
            ),
            PartStartEvent(index=0, part=TextPart(content='The run')),
        ]

    async def _run_and_capture(self, run_id, stream_factory):
        await run_store.register_run(run_id, 'thread-partial', 7)
        adapter = _StubAdapter(stream_factory, native_events=self._native_events())
        deps = AiChatDeps(thread_id='thread-partial', user_id=7, run_id=run_id)
        captured = []

        async def fake_persist(thread_id, messages):
            captured.append((thread_id, messages))

        with mock.patch.object(streaming, 'persist_messages', fake_persist):
            task = asyncio.ensure_future(
                streaming.produce_run(
                    adapter, _StubAgent(), run_id, deps, streaming.RunOptions()
                )
            )
            if stream_factory is _wedged_stream:
                await asyncio.wait_for(_wedged_started.wait(), timeout=2)
                await run_store.request_cancel(run_id)
            await asyncio.wait_for(task, timeout=5)
        return captured

    def _assert_partial_turn(self, captured):
        self.assertEqual(len(captured), 1, 'expected exactly one persist call')
        thread_id, messages = captured[0]
        self.assertEqual(thread_id, 'thread-partial')
        transcript = serialize_messages(messages)
        parts = [part for message in transcript for part in message['parts']]
        # The streamed text (deltas applied) survives...
        self.assertIn('Looking it up', [p.get('content') for p in parts])
        # ...as does the tool call whose side effects already happened...
        self.assertIn('tc1', [p.get('id') for p in parts if p['type'] == 'tool-call'])
        # ...and the text the model had begun for the next step.
        self.assertIn('The run', [p.get('content') for p in parts])

    async def test_cancelled_run_persists_partial_output(self):
        _wedged_started.clear()
        captured = await self._run_and_capture('run-cancel', _wedged_stream)
        self.assertEqual(await run_store.run_status('run-cancel'), 'cancelled')
        self._assert_partial_turn(captured)

    async def test_failed_run_persists_partial_output(self):
        async def exploding_stream():
            msg = 'provider blew up'
            raise RuntimeError(msg)
            yield ''  # pragma: no cover - never reached

        captured = await self._run_and_capture('run-error', exploding_stream)
        self.assertEqual(await run_store.run_status('run-error'), 'error')
        self._assert_partial_turn(captured)

    async def test_successful_run_does_not_double_persist(self):
        async def stream():
            yield 'data: a\n\n'

        captured = await self._run_and_capture('run-ok', stream)
        self.assertEqual(await run_store.run_status('run-ok'), 'finished')
        # on_complete owns persistence on the happy path; the stub never calls it.
        self.assertEqual(captured, [])


class StreamRunEventsTest(IsolatedAsyncioTestCase):
    def setUp(self):
        server = fakeredis.FakeServer()
        run_store._aredis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        run_store._sredis = fakeredis.FakeRedis(server=server, decode_responses=True)
        # Shorten the Redis read block so stream_run_events checks the run status
        # (and discovers EOT) promptly instead of waiting for the 15 s default.
        self._orig_block = streaming._STREAM_BLOCK_MS  # type: ignore[attr-defined]
        streaming._STREAM_BLOCK_MS = 100  # type: ignore[attr-defined]

    def tearDown(self):
        streaming._STREAM_BLOCK_MS = self._orig_block  # type: ignore[attr-defined]
        run_store._aredis = None
        run_store._sredis = None

    async def _collect(self, run_id):
        return [chunk async for chunk in streaming.stream_run_events(run_id)]

    async def test_streams_all_events_for_the_run_that_started_the_request(self):
        """The direct POST stream drains this run and stops at its sentinel."""
        await run_store.register_run('run1', 't1', 7)
        await run_store.append_event('run1', 'data: first\n\n')
        await run_store.append_event('run1', 'data: second\n\n')
        await run_store.finish_run('run1', 'finished')

        self.assertEqual(
            await self._collect('run1'),
            ['data: first\n\n', 'data: second\n\n'],
        )


class RunOptionsModelSettingsTest(IsolatedAsyncioTestCase):
    """Per-run model settings must reach the underlying agent call.

    This is how a provider's conversation-scoped headers (e.g. OpenCode Go's
    `x-opencode-session`) get onto every request without being baked into the
    lru-cached agent.
    """

    def setUp(self):
        server = fakeredis.FakeServer()
        run_store._aredis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        run_store._sredis = fakeredis.FakeRedis(server=server, decode_responses=True)

    def tearDown(self):
        run_store._aredis = None
        run_store._sredis = None

    async def test_model_settings_are_passed_to_run_stream_native(self):
        async def stream():
            yield 'data: a\n\n'

        settings = {'extra_headers': {'x-opencode-session': 'thread-1'}}
        await run_store.register_run('run-ms', 'thread-1', 7)
        adapter = _StubAdapter(stream)
        deps = AiChatDeps(thread_id='thread-1', user_id=7, run_id='run-ms')

        await streaming.produce_run(
            adapter,
            _StubAgent(),
            'run-ms',
            deps,
            streaming.RunOptions(model_settings=settings),
        )

        self.assertEqual(adapter.model_settings, settings)

    async def test_model_settings_default_to_none(self):
        async def stream():
            yield 'data: a\n\n'

        await run_store.register_run('run-none', 'thread-2', 7)
        adapter = _StubAdapter(stream)
        deps = AiChatDeps(thread_id='thread-2', user_id=7, run_id='run-none')

        await streaming.produce_run(
            adapter, _StubAgent(), 'run-none', deps, streaming.RunOptions()
        )

        self.assertIsNone(adapter.model_settings)
