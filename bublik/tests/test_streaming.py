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
from unittest import IsolatedAsyncioTestCase

import fakeredis
import fakeredis.aioredis

from bublik.ai import run_store, streaming
from bublik.ai.types import ChatDeps


class _StubAgent:
    """Stand-in for the pydantic-ai agent's ``async with`` (MCP) lifecycle."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _StubAdapter:
    """Minimal AGUIAdapter: exposes ``run_input`` and a caller-supplied stream."""

    def __init__(self, stream_factory):
        self.run_input = type('RunInput', (), {'messages': []})()
        self._stream_factory = stream_factory

    def run_stream(self, *, deps=None, capabilities=None, on_complete=None):
        return None

    def encode_stream(self, _stream):
        return self._stream_factory()


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
        deps = ChatDeps(thread_id='thread1', user_id=7, run_id='run1')

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
        deps = ChatDeps(thread_id='thread2', user_id=7, run_id='run2')

        await streaming.produce_run(adapter, _StubAgent(), 'run2', deps, streaming.RunOptions())

        self.assertEqual(await run_store.run_status('run2'), 'finished')
        entries = await self._drain('run2')
        _last_id, last_fields = entries[-1]
        self.assertEqual(last_fields.get(run_store.EOT_FIELD), 'finished')


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
