# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
The ``generate_file`` chat-agent tool: render agent-authored content to a
downloadable file, store it in S3 and record a
:class:`bublik.data.models.ChatFile` row.

This tool is chat-only: unlike the shared ``MCP_TOOLS`` it needs the run's
thread/user (via ``RunContext[ChatDeps]``), which the MCP server does not
have. The actual rendering (pdf/docx/xlsx/csv/md/html/json/txt) is pure and
CPU-bound and lives in :mod:`bublik.ai.rendering`; here we thread the run
context, enforce the size limit, persist to S3 and build the download URL.
"""

from __future__ import annotations

import uuid

import anyio
from asgiref.sync import sync_to_async
from django.conf import settings
from pydantic_ai import ModelRetry, RunContext

from bublik.ai.rendering import CONTENT_TYPES, FileFormat, render, sanitize_filename
from bublik.ai.types import ChatDeps
from bublik.core import s3
from bublik.data.models import ChatFile, ChatThread


__all__ = ['ChatDeps', 'download_url', 'generate_file']

# Default mirrors the settings templates; getattr so a stale generated
# settings.py (predating this feature) does not break the tool.
_DEFAULT_MAX_SIZE = 20 * 1024 * 1024


def _max_file_size() -> int:
    return getattr(settings, 'CHAT_FILE_MAX_SIZE', _DEFAULT_MAX_SIZE)


def download_url(file_id: str | uuid.UUID) -> str:
    """The user-facing download URL for a generated file.

    Deliberately origin-relative, NOT prefixed with ``BUBLIK_FQDN``: the auth
    cookie is ``SameSite=Strict``, so the link only works when it stays on the
    exact origin the user is browsing (an FQDN-absolute link breaks when the
    user reached the UI via a different host, e.g. localhost vs 127.0.0.1).
    """
    prefix = settings.URL_PREFIX.strip('/')
    base = f'/{prefix}/api/v2/chat/files' if prefix else '/api/v2/chat/files'
    return f'{base}/{file_id}'


@sync_to_async
def _persist(deps: ChatDeps, filename: str, content_type: str, data: bytes) -> ChatFile:
    thread, created = ChatThread.objects.get_or_create(
        pk=deps.thread_id,
        defaults={'user_id': deps.user_id},
    )
    if not created and thread.user_id != deps.user_id:
        msg = 'Thread ownership mismatch during file generation.'
        raise ModelRetry(msg)
    file_id = uuid.uuid4()
    key = s3.chat_file_key(str(thread.pk), str(file_id), filename)
    s3.upload_bytes(key, data, content_type)
    return ChatFile.objects.create(
        id=file_id,
        thread=thread,
        filename=filename,
        content_type=content_type,
        size=len(data),
        s3_key=key,
    )


async def generate_file(
    ctx: RunContext[ChatDeps],
    filename: str,
    file_format: FileFormat,
    content: str | None = None,
    rows: list[list[str | int | float | None]] | None = None,
    title: str | None = None,
) -> dict:
    """
    Generate a downloadable file for the user and store it.

    Input per format:
    - pdf, docx, md: pass the document as Markdown in `content`
      (headings, tables and fenced code blocks are supported; prefer pdf
      over docx for code-heavy documents).
    - html, json, txt: pass the raw text in `content`.
    - xlsx, csv: pass tabular data in `rows`; the first row must be the
      column headers.

    Never embed remote images or stylesheets; they are rejected.

    Args:
        filename: Suggested filename; its extension is normalized to the format
        file_format: One of pdf, docx, xlsx, md, html, csv, json, txt
        content: Document text (Markdown or raw, depending on the format)
        rows: Tabular data for xlsx/csv; first row is the header row
        title: Optional document title (PDF metadata / spreadsheet sheet name)

    Returns:
        File metadata with `download_url` -- always give the user this link
        as `[filename](download_url)` in your answer.
    """
    if ctx.deps is None:
        msg = 'File generation is not available in this context.'
        raise ModelRetry(msg)

    safe_name = sanitize_filename(filename, file_format)
    doc_title = title or safe_name.rsplit('.', 1)[0]

    data = await anyio.to_thread.run_sync(
        render,
        file_format,
        content,
        rows,
        doc_title,
    )
    max_size = _max_file_size()
    if len(data) > max_size:
        msg = (
            f'The generated file is too large '
            f'({len(data)} > {max_size} bytes); '
            f'produce a smaller document.'
        )
        raise ModelRetry(msg)

    chat_file = await _persist(
        ctx.deps,
        safe_name,
        CONTENT_TYPES[file_format],
        data,
    )
    return {
        'file_id': str(chat_file.id),
        'filename': chat_file.filename,
        'content_type': chat_file.content_type,
        'size': chat_file.size,
        'download_url': download_url(chat_file.id),
    }
