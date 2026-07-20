# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Storage seam for AI-chat generated files.

Every caller goes through this module rather than a concrete backend. Which
backend is used is stated outright by ``AI_CHAT_FILE_STORAGE_BACKEND``:

* ``local`` -> :mod:`bublik.core.local_storage`, writing under
  ``AI_CHAT_FILE_STORAGE_DIR``;
* ``s3`` -> :mod:`bublik.core.s3`, using ``S3_ENDPOINT_URL`` (the bundled
  SeaweedFS, or any S3-compatible endpoint including real AWS S3).

A backend that is selected but not configured raises rather than quietly
falling back: silently writing to local disk because an S3 endpoint was
mistyped is the failure this setting exists to prevent.

Backends are imported lazily so a deployment that never uses S3 does not pay
for boto3 (nor require it to be importable at model-load time).

Object keys are backend-neutral: the same ``chat/<thread>/<file>/<name>``
string is a bucket key for S3 and a path relative to the storage root on disk.

All functions are synchronous; async callers must run them in a thread
(``anyio.to_thread.run_sync`` / ``sync_to_async``).
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


__all__ = [
    'LOCAL_BACKEND',
    'S3_BACKEND',
    'backend_name',
    'chat_file_key',
    'delete_prefix',
    'public_download_url',
    'read_object',
    'setting',
    'upload_bytes',
    'use_s3',
]

LOCAL_BACKEND = 'local'
S3_BACKEND = 's3'

# getattr with defaults (matching the settings templates) so a deployment
# running a settings.py generated before these settings existed degrades
# sensibly instead of raising AttributeError.
_DEFAULTS = {
    # Empty means the setting is absent altogether, not that no backend was
    # chosen; backend_name() then infers one, see there.
    'AI_CHAT_FILE_STORAGE_BACKEND': '',
    'S3_ENDPOINT_URL': '',
    'S3_PUBLIC_ENDPOINT_URL': '',
    'S3_ACCESS_KEY': 'bublik',
    'S3_SECRET_KEY': 'bublik-secret-key',
    'S3_BUCKET': 'bublik-ai-chat-files',
    'S3_REGION': 'us-east-1',
    'S3_PRESIGN_EXPIRY': 300,
    # No usable default on purpose; see local_storage.root().
    'AI_CHAT_FILE_STORAGE_DIR': '',
}


def setting(name: str) -> str | int:
    return getattr(settings, name, _DEFAULTS[name])


def backend_name() -> str:
    """The selected storage backend, or raise if the selection is unusable.

    An empty value means the deployment's settings.py predates the setting.
    Such an installation is read the way it was configured at the time: an
    S3 endpoint being present is what selected object storage back then.
    """
    configured = str(setting('AI_CHAT_FILE_STORAGE_BACKEND')).strip().lower()
    if not configured:
        return S3_BACKEND if setting('S3_ENDPOINT_URL') else LOCAL_BACKEND
    if configured not in (LOCAL_BACKEND, S3_BACKEND):
        msg = (
            f'AI_CHAT_FILE_STORAGE_BACKEND is {configured!r}, which is not a '
            f'storage backend. Set it to {LOCAL_BACKEND!r} to keep generated '
            f'files on local disk, or to {S3_BACKEND!r} to store them in '
            f'S3-compatible object storage.'
        )
        raise ImproperlyConfigured(msg)
    if configured == S3_BACKEND and not setting('S3_ENDPOINT_URL'):
        msg = (
            'AI_CHAT_FILE_STORAGE_BACKEND is "s3", but S3_ENDPOINT_URL is '
            'empty, so there is no object store to write generated files to. '
            'Set S3_ENDPOINT_URL, or select the "local" backend.'
        )
        raise ImproperlyConfigured(msg)
    return configured


def use_s3() -> bool:
    """Whether generated files go to object storage rather than local disk."""
    return backend_name() == S3_BACKEND


def _backend():
    """The active storage backend module."""
    if use_s3():
        from bublik.core import s3  # noqa: PLC0415

        return s3
    from bublik.core import local_storage  # noqa: PLC0415

    return local_storage


def chat_file_key(thread_id: str, file_id: str, filename: str) -> str:
    """Key for a generated chat file; prefixed per-thread so cleanup is a prefix."""
    return f'chat/{thread_id}/{file_id}/{filename}'


def thread_prefix(thread_id: str) -> str:
    """The key prefix holding every generated file of one thread."""
    return f'chat/{thread_id}/'


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    """Store one object."""
    _backend().upload_bytes(key, data, content_type)


def read_object(key: str) -> bytes:
    """Read a whole object into memory (objects are capped by AI_CHAT_FILE_MAX_SIZE)."""
    return _backend().read_object(key)


def delete_prefix(prefix: str) -> None:
    """Best-effort bulk delete of every object under ``prefix``."""
    _backend().delete_prefix(prefix)


def public_download_url(key: str, filename: str) -> str | None:
    """A browser-reachable URL for the object, or ``None`` to proxy it instead.

    Only S3 with a configured public endpoint can hand the browser a URL of
    its own; local files, and S3 endpoints that are not browser-reachable
    (the bundled SeaweedFS listens on loopback), must be proxied by the
    download endpoint.
    """
    return _backend().public_download_url(key, filename)
