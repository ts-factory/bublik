# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Unit tests for the Redis-backed resumable-run store (:mod:`bublik.ai.run_store`).

Backed by ``fakeredis`` (a shared :class:`fakeredis.FakeServer` so the async ASGI
client and the sync DRF client see the same data), so no live Redis is needed.
"""

import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import fakeredis
import fakeredis.aioredis

from bublik.ai import run_store


class RedisUrlTest(TestCase):
    @mock.patch.object(run_store, 'settings', SimpleNamespace())
    def test_defaults_to_legacy_local_redis(self):
        self.assertEqual(run_store._redis_url(), 'redis://127.0.0.1:6379/0')

    @mock.patch.object(
        run_store,
        'settings',
        SimpleNamespace(REDIS_URL='redis://redis.example:6380/2'),
    )
    def test_uses_configured_redis_url(self):
        self.assertEqual(run_store._redis_url(), 'redis://redis.example:6380/2')


class RunStoreTest(IsolatedAsyncioTestCase):
    def setUp(self):
        server = fakeredis.FakeServer()
        # Bypass the lazy `settings.REDIS_URL` factories by seeding the module
        # singletons with fakeredis clients sharing one server.
        run_store._aredis = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        run_store._sredis = fakeredis.FakeRedis(server=server, decode_responses=True)

    def tearDown(self):
        run_store._aredis = None
        run_store._sredis = None

    async def _drain(self, run_id):
        """Read the whole buffer from the start (non-blocking) as a flat list."""
        return await run_store.read(run_id, '0', 1)

    async def test_register_sets_running_status_and_thread_pointer(self):
        await run_store.register_run('run1', 'thread1', 7)
        self.assertEqual(await run_store.run_status('run1'), 'running')
        self.assertEqual(await run_store.get_thread_run('thread1'), 'run1')

    async def test_register_rejects_existing_running_run(self):
        await run_store.register_run('run1', 'thread1', 7)

        with self.assertRaises(run_store.ConcurrentRunError):
            await run_store.register_run('run2', 'thread1', 7)

        self.assertEqual(await run_store.get_thread_run('thread1'), 'run1')
        self.assertIsNone(await run_store.run_status('run2'))

    async def test_concurrent_registration_has_one_winner(self):
        results = await asyncio.gather(
            run_store.register_run('run1', 'thread1', 7),
            run_store.register_run('run2', 'thread1', 7),
            return_exceptions=True,
        )

        errors = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], run_store.ConcurrentRunError)
        winner = await run_store.get_thread_run('thread1')
        self.assertIn(winner, {'run1', 'run2'})
        loser = 'run2' if winner == 'run1' else 'run1'
        self.assertEqual(await run_store.run_status(winner), 'running')
        self.assertIsNone(await run_store.run_status(loser))

    async def test_append_then_read_returns_events_in_order(self):
        await run_store.register_run('run1', 'thread1', 7)
        await run_store.append_event('run1', 'event-a')
        await run_store.append_event('run1', 'event-b')
        entries = await self._drain('run1')
        data = [fields[run_store.DATA_FIELD] for _id, fields in entries]
        self.assertEqual(data, ['event-a', 'event-b'])

    async def test_finish_writes_sentinel_and_final_status(self):
        await run_store.register_run('run1', 'thread1', 7)
        await run_store.append_event('run1', 'event-a')
        await run_store.finish_run('run1', 'finished')

        self.assertEqual(await run_store.run_status('run1'), 'finished')
        entries = await self._drain('run1')
        # Last entry is the terminal sentinel, not a data event.
        _last_id, last_fields = entries[-1]
        self.assertIn(run_store.EOT_FIELD, last_fields)
        self.assertEqual(last_fields[run_store.EOT_FIELD], 'finished')

    async def test_error_status_is_recorded(self):
        await run_store.register_run('run1', 'thread1', 7)
        await run_store.finish_run('run1', 'error')
        self.assertEqual(await run_store.run_status('run1'), 'error')

    async def test_read_times_out_to_empty_list(self):
        await run_store.register_run('run1', 'thread1', 7)
        # No entries after the current tail -> blocks briefly, returns empty.
        entries = await run_store.read('run1', '$', 1)
        self.assertEqual(entries, [])

    async def test_missing_run_status_is_none(self):
        self.assertIsNone(await run_store.run_status('does-not-exist'))

    async def test_active_run_only_while_running(self):
        await run_store.register_run('run1', 'thread1', 7)
        # Sync helper (DRF views) sees the async-written data via shared server.
        self.assertEqual(run_store.active_run('thread1'), 'run1')
        await run_store.finish_run('run1', 'finished')
        self.assertIsNone(run_store.active_run('thread1'))

    async def test_latest_run_status_includes_terminal_state(self):
        await run_store.register_run('run1', 'thread1', 7)
        self.assertEqual(run_store.latest_run_status('thread1'), 'running')
        await run_store.finish_run('run1', 'cancelled')
        self.assertEqual(run_store.latest_run_status('thread1'), 'cancelled')

    async def test_active_streaming_threads_filters_to_running(self):
        await run_store.register_run('run1', 'threadA', 7)
        await run_store.register_run('run2', 'threadB', 7)
        await run_store.finish_run('run2', 'finished')
        streaming = run_store.active_streaming_threads(['threadA', 'threadB', 'threadC'])
        self.assertEqual(streaming, {'threadA'})

    async def test_active_streaming_threads_empty_input(self):
        self.assertEqual(run_store.active_streaming_threads([]), set())

    async def test_cancel_flag_round_trips(self):
        self.assertFalse(await run_store.is_cancel_requested('run1'))
        await run_store.request_cancel('run1')
        self.assertTrue(await run_store.is_cancel_requested('run1'))

    async def test_active_run_async_only_while_running(self):
        await run_store.register_run('run1', 'thread1', 7)
        self.assertEqual(await run_store.active_run_async('thread1'), 'run1')
        await run_store.finish_run('run1', 'finished')
        self.assertIsNone(await run_store.active_run_async('thread1'))

    async def test_active_run_async_none_for_unknown_thread(self):
        self.assertIsNone(await run_store.active_run_async('does-not-exist'))
