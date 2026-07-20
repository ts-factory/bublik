# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Download endpoint for chat-generated files.

Serves a :class:`bublik.data.models.ChatFile` to its owner, either by proxying
the object through this endpoint or by redirecting to a presigned URL when a
public S3 endpoint is configured. Ownership is enforced in
:mod:`bublik.ai.access`; the route is wired in :mod:`bublik.ai.app`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import anyio
from django.conf import settings
from starlette.responses import JSONResponse, RedirectResponse, Response

from bublik.ai.access import get_owned_file, resolve_user
from bublik.core import s3


if TYPE_CHECKING:
    from starlette.requests import Request


async def download_file(request: Request) -> Response:
    """Serve a generated chat file to its owner.

    Ownership is enforced via the file's thread (404 otherwise, hiding
    existence). By default the object is proxied through this endpoint: the
    bundled MinIO listens on loopback only, so a presigned URL against it
    would be unreachable from the browser (SigV4 signs the Host header).
    When ``S3_PUBLIC_ENDPOINT_URL`` is configured (real S3), the request is
    redirected to a short-lived presigned URL instead. The full in-memory
    read is bounded by ``CHAT_FILE_MAX_SIZE`` enforced at generation time.
    """
    user = await resolve_user(request)
    if user is None:
        # Browser navigations (markdown links open the URL directly, outside
        # the SPA's token-refresh fetch layer) get bounced to the login page
        # and back instead of a bare 401 JSON body. Programmatic clients
        # (FileCard's fetch) still receive the 401 and refresh themselves.
        if 'text/html' in request.headers.get('accept', ''):
            prefix = settings.URL_PREFIX.strip('/')
            login = f'/{prefix}/v2/auth/login' if prefix else '/v2/auth/login'
            return RedirectResponse(
                f'{login}?redirect_url={quote(str(request.url), safe="")}',
                status_code=302,
            )
        return JSONResponse({'detail': 'Authentication required.'}, status_code=401)

    chat_file = await get_owned_file(request.path_params['file_id'], user.id)
    if chat_file is None:
        return JSONResponse({'detail': 'Not found.'}, status_code=404)

    if s3.setting('S3_PUBLIC_ENDPOINT_URL'):
        url = await anyio.to_thread.run_sync(
            s3.presign_public_get,
            chat_file.s3_key,
            chat_file.filename,
        )
        return RedirectResponse(url, status_code=307)

    data = await anyio.to_thread.run_sync(s3.read_object, chat_file.s3_key)
    disposition = f"attachment; filename*=UTF-8''{quote(chat_file.filename)}"
    return Response(
        data,
        media_type=chat_file.content_type,
        headers={'Content-Disposition': disposition},
    )
