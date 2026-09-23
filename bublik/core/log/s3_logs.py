# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Serving the logs of runs stored in S3.

For runs on a TE log server, Bublik hands the browser URLs on that server,
whose 404 handler generates JSON and HTML log pages on demand from the run's
``raw_log_bundle.tpxz`` (``rgt-log-get-item``). S3 has no such handler, so for
runs under an S3 logs base Bublik hands out URLs of its own endpoint
(:func:`browser_url`) and does the same job itself (:func:`open_log_item`):
an object that exists is streamed; a missing ``json/`` or ``html/`` item is
generated from the bundle, cached locally, and written back to S3 so the next
request is a plain object read.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import mimetypes
import os
import subprocess
import tempfile
from typing import IO, TYPE_CHECKING
from urllib.parse import quote

from django.conf import settings

from bublik.core import s3
from bublik.core.exceptions import BublikServerError
from bublik.core.logging import get_task_or_server_logger
from bublik.core.logs_storage import find_s3_storage, is_s3_url, parse_s3_url


if TYPE_CHECKING:
    from bublik.core.logs_storage import LogsStorage


ROUTE = 'api/v2/logs/s3/'
BUNDLE = 'raw_log_bundle.tpxz'
GENERATED_DIRS = ('json', 'html')
GENERATOR = 'rgt-log-get-item'
DEFAULT_GENERATION_TIMEOUT = 600


class LogGenerationError(BublikServerError):
    """A log item could not be generated from the run's bundle."""


@dataclass
class LogItem:
    """An open log item: a stream to copy to the client, and how to label it."""

    stream: IO[bytes]
    content_type: str
    length: int | None = None


def browser_url(url: str | None, origin: str | None = None) -> str | None:
    """The URL a browser can open for a log ``url``.

    ``s3://`` URLs become URLs of Bublik's own endpoint; anything else (a TE
    log server) is returned unchanged. ``origin`` is Bublik's
    ``scheme://host/prefix/``; without it the URL is host-relative.
    """
    if not url or not is_s3_url(url):
        return url
    bucket, key = parse_s3_url(url)
    path = f'{ROUTE}{quote(bucket)}/{quote(key)}'
    if origin:
        return origin.rstrip('/') + '/' + path
    prefix = f'/{settings.URL_PREFIX}' if settings.URL_PREFIX else ''
    return f'{prefix}/{path}'


def direct_url(url: str, project_id: int | None = None) -> str | None:
    """A presigned URL on the storage's public endpoint for an existing log item.

    ``None`` when the storage has no public endpoint or the item does not
    exist yet (it must go through Bublik to be generated). Only for single
    files: pages that link to other, not yet generated, pages (HTML) must keep
    going through Bublik.
    """
    storage = find_s3_storage(url, project_id)
    if storage is None or not storage.target.public_endpoint_url:
        return None
    key = storage.key(url)
    if not s3.object_exists(key, target=storage.target):
        return None
    return s3.public_read_url(key, target=storage.target)


def storage_for(bucket: str, key: str) -> LogsStorage | None:
    """The S3 logs base holding ``key``; only keys under a configured base are served."""
    return find_s3_storage(f's3://{bucket}/{key}')


def split_generated(key: str) -> tuple[str, str] | None:
    """Split ``<run prefix>/json/<item>`` into the run prefix and ``json/<item>``.

    ``None`` if ``key`` is not a generated log item, or is unsafe to map onto
    the local cache.
    """
    parts = key.split('/')
    if any(part in ('', '.', '..') for part in parts[:-1]) or parts[-1] in ('.', '..'):
        return None
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] in GENERATED_DIRS:
            run_prefix = '/'.join(parts[:index])
            item = '/'.join(parts[index:])
            return (run_prefix + '/' if run_prefix else ''), item
    return None


def content_type_of(name: str) -> str:
    content_type, _ = mimetypes.guess_type(name)
    if content_type in ('text/html', 'text/plain', 'application/json', 'text/css'):
        return f'{content_type}; charset=utf-8'
    return content_type or 'application/octet-stream'


def open_log_item(storage: LogsStorage, key: str) -> LogItem | None:
    """Open a log item for streaming, generating it if it is a missing page.

    ``None`` if it does not exist and cannot be generated. Raises
    :class:`LogGenerationError` if generation was attempted and failed.
    """
    target = storage.target
    obj = s3.open_object(key, target=target)
    if obj is not None:
        return LogItem(
            stream=obj['Body'],
            content_type=obj.get('ContentType') or content_type_of(key),
            length=obj.get('ContentLength'),
        )

    split = split_generated(key)
    if split is None:
        return None
    run_prefix, item = split
    path = generate_item(storage, run_prefix, item)
    if path is None:
        return None

    content_type = content_type_of(path)
    with open(path, 'rb') as generated:
        s3.upload_fileobj(key, generated, content_type, target=target)
    return LogItem(
        stream=open(path, 'rb'),
        content_type=content_type,
        length=os.path.getsize(path),
    )


def cache_root() -> str:
    """Where bundles are cached and pages generated. Per deployment, never shared /tmp."""
    return getattr(
        settings,
        'LOGS_S3_CACHE_DIR',
        os.path.join(settings.BASE_DIR, 'logs-s3-cache'),
    )


@contextmanager
def _run_lock(run_dir: str):
    """Serialize generation for one run, across threads and worker processes."""
    with open(os.path.join(run_dir, '.lock'), 'w') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def generate_item(storage: LogsStorage, run_prefix: str, item: str) -> str | None:
    """Generate ``item`` of the run at ``run_prefix``; return its local path.

    ``None`` if the run has no bundle to generate from.
    """
    run_dir = os.path.join(cache_root(), storage.target.bucket, *run_prefix.split('/'))
    item_path = os.path.normpath(os.path.join(run_dir, *item.split('/')))
    if not item_path.startswith(os.path.normpath(run_dir) + os.sep):
        return None
    if os.path.isfile(item_path):
        return item_path

    os.makedirs(run_dir, mode=0o700, exist_ok=True)
    with _run_lock(run_dir):
        # Another request may have generated it while this one waited.
        if os.path.isfile(item_path):
            return item_path
        bundle_path = os.path.join(run_dir, BUNDLE)
        if not os.path.isfile(bundle_path) and not _download_bundle(
            storage,
            run_prefix + BUNDLE,
            bundle_path,
        ):
            return None
        _run_generator(bundle_path, item_path)
    return item_path if os.path.isfile(item_path) else None


def _download_bundle(storage: LogsStorage, key: str, path: str) -> bool:
    if not s3.object_exists(key, target=storage.target):
        return False
    fd, partial = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.part')
    os.close(fd)
    try:
        s3.download_file(key, partial, target=storage.target)
        os.replace(partial, path)
    finally:
        if os.path.exists(partial):
            os.unlink(partial)
    return True


def _run_generator(bundle_path: str, item_path: str) -> None:
    logger = get_task_or_server_logger()
    command = [GENERATOR, f'--bundle={bundle_path}', f'--req-path={item_path}']
    timeout = getattr(settings, 'LOGS_S3_GENERATION_TIMEOUT', DEFAULT_GENERATION_TIMEOUT)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        msg = f'failed to generate {os.path.basename(item_path)}: {error}'
        logger.error(msg)
        raise LogGenerationError(message=msg) from error
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout).strip()[-2000:]
        msg = f'failed to generate {os.path.basename(item_path)}'
        logger.error(f'{msg}: {details}')
        raise LogGenerationError(message=msg, debug_details=[details])


def list_run_directory(storage: LogsStorage, prefix: str) -> tuple[list[str], list[str]]:
    """Sub-directories and files under ``prefix``, as names relative to it."""
    prefixes, keys = s3.list_entries(prefix, target=storage.target)
    directories = [p[len(prefix) :] for p in prefixes]
    files = [k[len(prefix) :] for k in keys if k != prefix]
    if BUNDLE in files:
        # Like a TE log server, offer the pages the bundle can produce.
        directories = sorted({*directories, *(f'{d}/' for d in GENERATED_DIRS)})
    return directories, files
