# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Redis-backed store for resumable AI chat runs.

A chat run is executed in a background task (see :mod:`bublik.ai.app`) that is
decoupled from the HTTP request that started it, so it keeps going when the
client navigates away or reloads. Every AG-UI SSE event is appended to a Redis
Stream, which bridges the background task to its original POST response.

Because the app is served by several uvicorn workers, the background task and
its POST response may run on different workers, so Redis is their shared bus.
Nothing about a run lives in process memory.

Keys (all under the ``chat`` prefix), each with ``_EVENT_TTL`` second expiry:

* ``chat:run:{run_id}``           -- Stream of events; each entry is either
                                     ``{data: <sse string>}`` or the terminal
                                     ``{eot: <status>}`` sentinel.
* ``chat:run:{run_id}:meta``      -- Hash: ``status`` (running/finished/error),
                                     ``thread_id``, ``user_id``.
* ``chat:thread:{thread_id}:run`` -- The thread's latest ``run_id`` (streaming
                                      indicator and cancellation source).
"""

from __future__ import annotations

import typing

from django.conf import settings
import redis
import redis.asyncio as aredis
from redis.exceptions import WatchError


if typing.TYPE_CHECKING:
    from collections.abc import Iterable

# Terminal stream field: presence marks the end of the run's event buffer.
EOT_FIELD = 'eot'
# Live-event stream field: the encoded AG-UI SSE chunk.
DATA_FIELD = 'data'


class ConcurrentRunError(Exception):
    """Raised by :func:`register_run` when a thread already has an active run."""


# How long a run's event buffer / active-run pointer live (seconds). Covers
# "leave the thread and come back" while keeping Redis from growing unboundedly.
_EVENT_TTL = 60 * 60

# Key namespace for every run-store key.
_PREFIX = 'chat'
_DEFAULT_REDIS_URL = 'redis://127.0.0.1:6379/0'

# Async client used by the ASGI chat routes (bublik.ai.app), which always run on
# the same persistent uvicorn worker event loop. A `redis.asyncio` client is bound
# to one loop, so it must NOT be shared with the sync DRF views (which run on
# ephemeral `async_to_sync` loops) -- those use the sync client below instead.
_aredis: aredis.Redis | None = None
_sredis: redis.Redis | None = None


def _redis_url() -> str:
    """Use the legacy local Redis address when old settings lack REDIS_URL."""
    return getattr(settings, 'REDIS_URL', _DEFAULT_REDIS_URL)


def _client() -> aredis.Redis:
    """Lazily build the per-process async Redis client (ASGI worker loop)."""
    global _aredis  # noqa: PLW0603
    if _aredis is None:
        _aredis = aredis.from_url(_redis_url(), decode_responses=True)
    return _aredis


def _sync_client() -> redis.Redis:
    """Lazily build the per-process sync Redis client (DRF views; thread-safe pool)."""
    global _sredis  # noqa: PLW0603
    if _sredis is None:
        _sredis = redis.from_url(_redis_url(), decode_responses=True)
    return _sredis


def _run_key(run_id: str) -> str:
    return f'{_PREFIX}:run:{run_id}'


def _meta_key(run_id: str) -> str:
    return f'{_PREFIX}:run:{run_id}:meta'


def _thread_key(thread_id: str) -> str:
    return f'{_PREFIX}:thread:{thread_id}:run'


def _cancel_key(run_id: str) -> str:
    return f'{_PREFIX}:run:{run_id}:cancel'


async def register_run(run_id: str, thread_id: str, user_id: int) -> None:
    """Mark ``run_id`` as the thread's active (running) run.

    Raises ``ConcurrentRunError`` when the thread already has a run in progress,
    so ``_run_chat`` can return ``409`` instead of silently orphaning the earlier
    run.
    """
    client = _client()
    thread_key = _thread_key(thread_id)
    meta_key = _meta_key(run_id)
    while True:
        async with client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(thread_key)
                existing_run_id = await pipe.get(thread_key)
                if existing_run_id:
                    existing_meta_key = _meta_key(existing_run_id)
                    await pipe.watch(existing_meta_key)
                    existing_status = await pipe.hget(existing_meta_key, 'status')
                    if existing_status == 'running' and existing_run_id != run_id:
                        msg = (
                            f'Thread {thread_id} already has an active run '
                            f'{existing_run_id}; rejecting concurrent run {run_id}.'
                        )
                        raise ConcurrentRunError(msg)

                pipe.multi()
                pipe.hset(
                    meta_key,
                    mapping={'status': 'running', 'thread_id': thread_id, 'user_id': user_id},
                )
                pipe.expire(meta_key, _EVENT_TTL)
                pipe.set(thread_key, run_id, ex=_EVENT_TTL)
                await pipe.execute()
                return
            except WatchError:
                continue


async def append_event(run_id: str, sse: str) -> None:
    """Append one encoded AG-UI SSE event to the run's buffer."""
    client = _client()
    run_key = _run_key(run_id)
    async with client.pipeline(transaction=True) as pipe:
        pipe.xadd(run_key, {DATA_FIELD: sse})
        pipe.expire(run_key, _EVENT_TTL)
        await pipe.execute()


async def finish_run(run_id: str, status: str = 'finished') -> None:
    """Append the terminal sentinel and record the final status.

    The buffer is kept briefly so the original POST response can drain events
    emitted just before the terminal sentinel.
    """
    client = _client()
    run_key = _run_key(run_id)
    meta_key = _meta_key(run_id)
    thread_id = await client.hget(meta_key, 'thread_id')
    async with client.pipeline(transaction=True) as pipe:
        pipe.xadd(run_key, {EOT_FIELD: status})
        pipe.hset(meta_key, 'status', status)
        pipe.expire(run_key, _EVENT_TTL)
        pipe.expire(meta_key, _EVENT_TTL)
        if thread_id:
            pipe.expire(_thread_key(thread_id), _EVENT_TTL)
        await pipe.execute()


async def get_thread_run(thread_id: str) -> str | None:
    """The thread's latest run id (running or finished), or ``None``."""
    return await _client().get(_thread_key(thread_id))


async def request_cancel(run_id: str) -> None:
    """Flag ``run_id`` for cancellation.

    The run's background task lives in a single uvicorn worker while the cancel
    request may hit any worker, so the intent is recorded in Redis; the worker
    that owns the run polls this flag (see :func:`bublik.ai.streaming._watch_cancel`)
    and cancels its own stream loop. TTL-bounded like the other run keys so a flag
    for a run that already finished cannot linger.
    """
    await _client().set(_cancel_key(run_id), '1', ex=_EVENT_TTL)


async def is_cancel_requested(run_id: str) -> bool:
    """Whether cancellation has been requested for ``run_id``."""
    return bool(await _client().exists(_cancel_key(run_id)))


async def active_run_async(thread_id: str) -> str | None:
    """The thread's run id, but only while it is still ``running``; else ``None``.

    Async twin of :func:`active_run` for the ASGI chat routes (which run on the
    persistent worker loop and must not touch the sync client).
    """
    run_id = await get_thread_run(thread_id)
    if run_id and await run_status(run_id) == 'running':
        return run_id
    return None


async def run_status(run_id: str) -> str | None:
    """The run's status (``running``/``finished``/``error``), or ``None`` if expired."""
    return await _client().hget(_meta_key(run_id), 'status')


def active_run(thread_id: str) -> str | None:
    """The thread's run id, but only while it is still ``running``; else ``None``.

    Sync (called from DRF views).
    """
    client = _sync_client()
    run_id = client.get(_thread_key(thread_id))
    if run_id and client.hget(_meta_key(run_id), 'status') == 'running':
        return run_id
    return None


def latest_run_status(thread_id: str) -> str | None:
    """Status of a thread's latest run, including terminal states."""
    client = _sync_client()
    run_id = client.get(_thread_key(thread_id))
    if not run_id:
        return None
    return client.hget(_meta_key(run_id), 'status')


async def read(
    run_id: str,
    last_id: str,
    block_ms: int,
) -> list[tuple[str, dict[str, str]]]:
    """Read stream entries after ``last_id``, blocking up to ``block_ms`` for new ones.

    Returns a list of ``(entry_id, fields)`` tuples (empty on timeout).
    """
    result = await _client().xread({_run_key(run_id): last_id}, block=block_ms)
    if not result:
        return []
    # xread returns one (stream key, entries) pair per stream; we read one stream,
    # so take that single pair's entries list.
    return result[0][1]


def active_streaming_threads(thread_ids: Iterable[str]) -> set[str]:
    """Subset of ``thread_ids`` whose latest run is still ``running``.

    Sync (called from the DRF sidebar list view).
    """
    thread_ids = list(thread_ids)
    if not thread_ids:
        return set()
    client = _sync_client()
    pipe = client.pipeline(transaction=False)
    for thread_id in thread_ids:
        pipe.get(_thread_key(thread_id))
    run_ids = pipe.execute()

    pending = [(tid, rid) for tid, rid in zip(thread_ids, run_ids) if rid]
    if not pending:
        return set()
    pipe = client.pipeline(transaction=False)
    for _tid, rid in pending:
        pipe.hget(_meta_key(rid), 'status')
    statuses = pipe.execute()
    return {tid for (tid, _rid), status in zip(pending, statuses) if status == 'running'}
