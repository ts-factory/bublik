# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
AG-UI routes for the Bublik chat assistant.

Endpoints under ``/api/v2/chat`` (honouring ``URL_PREFIX``):

* ``GET  /api/v2/chat/models``    lists the configured providers and models for
  the UI (sanitized; no api urls or key references).
* ``POST /api/v2/chat``           starts an agent run for the selected
  ``provider``/``model``/``effort`` (query params) from the AG-UI ``RunAgentInput``
  body and streams its events to that request. The run itself remains a
  background task, so it persists the final transcript after disconnect/reload.
* ``POST /api/v2/chat/cancel``     requests cancellation of a thread's in-flight
  run (``thread`` query param). The background run task tears itself down; a
  terminal error event is buffered so the client leaves the streaming state.
* ``GET  /api/v2/chat/files/{file_id}`` serves a chat-generated file to its owner.

This module is the thin route table; the machinery lives in sibling modules:
authorization in :mod:`bublik.ai.access`, run/SSE streaming in
:mod:`bublik.ai.streaming`, and file download in :mod:`bublik.ai.downloads`.

The client speaks AG-UI through a standard streaming ``POST /chat`` request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from pydantic_ai.ui.ag_ui import AGUIAdapter
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from bublik.ai import run_store
from bublik.ai.access import resolve_user, user_may_access_thread
from bublik.ai.agent import build_agent
from bublik.ai.compaction import make_usage_reporter
from bublik.ai.config import (
    ModelRequestError,
    config_fingerprint,
    effective_ai_config,
    get_effective_ai_config,
    get_raw_ai_config,
    parse_ai_config,
    public_models,
    resolve_model_request,
)
from bublik.ai.downloads import download_file
from bublik.ai.streaming import RunOptions, spawn_run, stream_run_events
from bublik.ai.transcript import persist_messages
from bublik.ai.types import ChatDeps
from bublik.data.models import ChatThread


if TYPE_CHECKING:
    from starlette.requests import Request


logger = logging.getLogger(__name__)


async def _list_models(_request: Request) -> Response:
    """Return the sanitized list of configured (or discovered) providers and models."""
    config = await sync_to_async(get_effective_ai_config)()
    return JSONResponse(public_models(config))


async def _run_chat(request: Request) -> Response:  # noqa: PLR0911 - endpoint validation exits
    """Start a durable run and stream its live AG-UI events to this request."""
    user = await resolve_user(request)
    if user is None:
        return JSONResponse({'detail': 'Authentication required.'}, status_code=401)

    provider = request.query_params.get('provider')
    model = request.query_params.get('model')
    effort = request.query_params.get('effort') or None
    # The real conversation id comes from the query param, not AG-UI's body
    # `threadId` (which is a client-generated value unrelated to our thread/URL id).
    thread_id = request.query_params.get('thread')
    if not provider or not model:
        return JSONResponse(
            {'detail': 'Query params `provider` and `model` are required.'},
            status_code=422,
        )
    if not thread_id:
        return JSONResponse({'detail': 'Query param `thread` is required.'}, status_code=422)
    try:
        UUID(thread_id)
    except ValueError:
        return JSONResponse({'detail': 'Query param `thread` must be a UUID.'}, status_code=422)

    # Resolve the model entry up front: reject unknown providers/models/efforts
    # with a clear 422 and normalize `effort` (fall back to the configured
    # default) *before* build_agent, so its cache is only ever keyed by
    # canonical values -- never by free-form user input. Validation runs against
    # the effective config (discovered models included); the fingerprint keying
    # the agent cache stays a digest of the authored config.
    raw_config = await sync_to_async(get_raw_ai_config)()
    config = await sync_to_async(effective_ai_config)(parse_ai_config(raw_config))
    try:
        _provider, _model_entry, effort = resolve_model_request(config, provider, model, effort)
    except ModelRequestError as exc:
        return JSONResponse({'detail': str(exc)}, status_code=422)

    logger.info(
        'building agent with fqdn=%s prefix=%s provider=%s model=%s',
        getattr(settings, 'BUBLIK_FQDN', ''),
        settings.URL_PREFIX,
        provider,
        model,
    )

    try:
        agent = await sync_to_async(build_agent)(
            provider,
            model,
            effort,
            config_fingerprint(raw_config),
        )
    except ValueError as exc:
        return JSONResponse({'detail': str(exc)}, status_code=422)

    adapter = await AGUIAdapter.from_request(request, agent=agent)
    run_id = adapter.run_input.run_id

    # Create the thread row atomically before persisting the user turn. The DB
    # remains authoritative for ownership and the complete visible transcript.
    # If the thread already belongs to a different user, reject.
    thread, _created = await sync_to_async(ChatThread.objects.get_or_create)(
        pk=thread_id,
        defaults={'user_id': user.id},
    )
    if thread.user_id != user.id:
        return JSONResponse({'detail': 'Not found.'}, status_code=404)

    try:
        await run_store.register_run(run_id, thread_id, user.id)
    except run_store.ConcurrentRunError as exc:
        logger.warning('concurrent run rejected: %s', exc)
        return JSONResponse({'detail': str(exc)}, status_code=409)

    try:
        # Reloads deliberately never reconstruct partial output from SSE events.
        await persist_messages(thread_id, adapter.messages)
    except Exception:
        logger.exception('failed to persist chat input for run %s', run_id)
        await run_store.finish_run(run_id, 'error')
        return JSONResponse({'detail': 'Unable to save the chat message.'}, status_code=500)

    deps = ChatDeps(thread_id=thread_id, user_id=user.id, run_id=run_id)

    context_limit = _model_entry.limit.context if _model_entry.limit else None
    options = RunOptions(
        on_complete=make_usage_reporter(thread_id, provider, model, context_limit),
    )
    spawn_run(adapter, agent, run_id, deps, options)

    return StreamingResponse(
        stream_run_events(run_id),
        media_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
        },
    )


async def _cancel_chat(request: Request) -> Response:
    """Request cancellation of a thread's in-flight run.

    Idempotent: if the thread has no running run (already finished, or never
    started) it still returns success -- there is simply nothing to cancel. The
    owning worker's run task polls the flag and tears itself down (see
    :func:`bublik.ai.streaming.produce_run`).
    """
    user = await resolve_user(request)
    if user is None:
        return JSONResponse({'detail': 'Authentication required.'}, status_code=401)

    thread_id = request.query_params.get('thread')
    if not thread_id:
        return JSONResponse({'detail': 'Query param `thread` is required.'}, status_code=422)
    if not await user_may_access_thread(user, thread_id):
        return JSONResponse({'detail': 'Not found.'}, status_code=404)

    run_id = await run_store.active_run_async(thread_id)
    if run_id is not None:
        await run_store.request_cancel(run_id)
    return Response(status_code=202, media_type='text/event-stream')


def _chat_base_path() -> str:
    prefix = settings.URL_PREFIX.strip('/')
    return f'/{prefix}/api/v2/chat' if prefix else '/api/v2/chat'


def build_chat_routes() -> list[Route]:
    """Build the chat model-listing, run, cancellation and file routes."""
    base = _chat_base_path()
    return [
        Route(f'{base}/models', _list_models, methods=['GET'], name='chat-models'),
        Route(f'{base}/cancel', _cancel_chat, methods=['POST'], name='chat-cancel'),
        Route(
            f'{base}/files/{{file_id}}',
            download_file,
            methods=['GET'],
            name='chat-file-download',
        ),
        Route(base, _run_chat, methods=['POST'], name='chat-run'),
    ]
