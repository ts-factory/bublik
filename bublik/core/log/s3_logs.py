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
generated from the bundle into a local cache and served from there.

S3 keeps only what was uploaded: a TE tarball's ``meta_data.json`` and bundle,
or a bundle's ready-made pages and attachments. Generated pages are never
written to it. A run's cache folder (its downloaded bundle and the pages made
from it) is deleted once the run goes unused for ``LOGS_S3_CACHE_TTL`` seconds
(:func:`prune_cache`); opening the run again makes them anew.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from dataclasses import dataclass
import fcntl
import mimetypes
import os
import shutil
import subprocess
import tempfile
import threading
import time
from typing import IO, TYPE_CHECKING
from urllib.parse import quote

from botocore.exceptions import ClientError
from django.conf import settings

from bublik.core import s3
from bublik.core.exceptions import BublikServerError
from bublik.core.logging import get_task_or_server_logger
from bublik.core.logs_storage import find_s3_storage, is_s3_url, parse_s3_url


if TYPE_CHECKING:
    from bublik.core.logs_storage import S3LogsStorage


ROUTE = 'api/v2/logs/s3/'
BUNDLE = 'raw_log_bundle.tpxz'
GENERATED_DIRS = ('json', 'html')
GENERATOR = 'rgt-log-get-item'
DEFAULT_GENERATION_TIMEOUT = 600
# How long a run's cache folder outlives its last use, in seconds.
DEFAULT_CACHE_TTL = 3600
# How often requests sweep the cache for unused folders, at most.
PRUNE_INTERVAL = 300
# In a run's cache folder: the lock, whose mtime marks the last use.
LOCK = '.lock'
# In a run's cache folder: json/ or html/ was generated whole.
COMPLETE = '.{}-complete'
# In the cache root: the last sweep's time.
PRUNED = '.pruned'
# In a run's cache folder: where a page is generated before it is moved into place.
SCRATCH = '.generating-'

# One sweep at a time in this process.
_sweep = threading.Lock()


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
    log server) is returned unchanged. The URL is made absolute with
    ``origin``, the ``scheme://host`` of the request, or else with
    ``BUBLIK_FQDN``; with neither it is host-relative.
    """
    if not url or not is_s3_url(url):
        return url
    bucket, key = parse_s3_url(url)
    host = (origin or getattr(settings, 'BUBLIK_FQDN', '') or '').rstrip('/')
    prefix = f'/{settings.URL_PREFIX}' if settings.URL_PREFIX else ''
    return f'{host}{prefix}/{ROUTE}{quote(bucket)}/{quote(key)}'


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


def storage_for(bucket: str, key: str) -> S3LogsStorage | None:
    """The S3 logs base holding ``key``; only keys under a configured base are served.

    A key with ``.`` or ``..`` segments is not: a gateway might collapse it
    into one outside the base.
    """
    parts = key.split('/')
    if any(part in ('.', '..') for part in parts) or '' in parts[:-1]:
        return None
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


def open_log_item(storage: S3LogsStorage, key: str) -> LogItem | None:
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
    # json/ and html/ themselves are listed (list_generated_directory).
    if split is None or split[1] in GENERATED_DIRS:
        return None
    run_prefix, item = split
    top, _, below = item.partition('/')
    # A sweep may delete the folder between generating the page and opening
    # it; it is generated again then.
    for _ in range(2):
        if '/' in below:
            # Deeper than json/<page> or html/<page> (html/images/...): made
            # only with the whole directory.
            path = generate_item(storage, run_prefix, top)
            if path is not None:
                path = os.path.join(path, *below.split('/'))
        else:
            path = generate_item(storage, run_prefix, item)
        if path is None:
            return None
        try:
            stream = open(path, 'rb')  # noqa: SIM115
        except (FileNotFoundError, IsADirectoryError):
            continue
        maybe_prune_cache()
        return LogItem(
            stream=stream,
            content_type=content_type_of(path),
            length=os.fstat(stream.fileno()).st_size,
        )
    return None


def list_generated_directory(
    storage: S3LogsStorage,
    prefix: str,
) -> tuple[list[str], list[str]] | None:
    """Sub-directories and files of ``<run>/json/`` or ``<run>/html/`` (or below).

    The directory is generated whole first, as a TE log server does. ``None``
    if ``prefix`` is not in one, or its run has no bundle (a bundle's
    ready-made ``json/`` is listed from S3). Raises
    :class:`LogGenerationError` as :func:`open_log_item` does.
    """
    split = split_generated(prefix.rstrip('/'))
    if split is None:
        return None
    run_prefix, item = split
    top, _, below = item.partition('/')
    if not s3.object_exists(run_prefix + BUNDLE, target=storage.target):
        return None
    for _ in range(2):
        path = generate_item(storage, run_prefix, top)
        if path is None:
            return None
        if below:
            path = os.path.join(path, *below.split('/'))
        try:
            entries = sorted(os.scandir(path), key=lambda entry: entry.name)
        except NotADirectoryError:
            return None
        except FileNotFoundError:
            continue
        maybe_prune_cache()
        directories = [f'{entry.name}/' for entry in entries if entry.is_dir()]
        files = [entry.name for entry in entries if entry.is_file()]
        return directories, files
    return None


def cache_root() -> str:
    """Where bundles are cached and pages generated. Per deployment, never shared /tmp."""
    return getattr(
        settings,
        'LOGS_S3_CACHE_DIR',
        os.path.join(settings.BASE_DIR, 'logs-s3-cache'),
    )


def cache_ttl() -> int:
    return int(getattr(settings, 'LOGS_S3_CACHE_TTL', DEFAULT_CACHE_TTL))


def _same_file(opened: IO, path: str) -> bool:
    try:
        on_disk = os.stat(path)
    except FileNotFoundError:
        return False
    mine = os.fstat(opened.fileno())
    return (mine.st_dev, mine.st_ino) == (on_disk.st_dev, on_disk.st_ino)


@contextmanager
def _run_lock(run_dir: str):
    """Serialize work on one run's folder, across threads and worker processes.

    A sweep may delete the folder, lock included, while this waits for the
    lock: the folder is then made and locked anew.
    """
    lock_path = os.path.join(run_dir, LOCK)
    while True:
        try:
            os.makedirs(run_dir, mode=0o700, exist_ok=True)
            lock_file = open(lock_path, 'a')  # noqa: SIM115
        except FileNotFoundError:
            # A sweep removed a parent folder meanwhile.
            continue
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if _same_file(lock_file, lock_path):
            break
        lock_file.close()
    try:
        yield
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _mark_used(run_dir: str) -> None:
    with suppress(FileNotFoundError):
        os.utime(os.path.join(run_dir, LOCK))


def _is_generated(run_dir: str, item: str, item_path: str) -> bool:
    if item in GENERATED_DIRS:
        return os.path.isfile(os.path.join(run_dir, COMPLETE.format(item)))
    return os.path.isfile(item_path)


def generate_item(storage: S3LogsStorage, run_prefix: str, item: str) -> str | None:
    """Generate ``item`` of the run at ``run_prefix`` into the cache; return its local path.

    ``item`` is a file under ``json/`` or ``html/``, or one of those
    directories, generated whole. Marks the run's folder used. ``None`` if the
    run has no bundle to generate from.
    """
    run_dir = os.path.join(cache_root(), storage.target.bucket, *run_prefix.split('/'))
    item_path = os.path.normpath(os.path.join(run_dir, *item.split('/')))
    if not item_path.startswith(os.path.normpath(run_dir) + os.sep):
        return None
    if _is_generated(run_dir, item, item_path):
        _mark_used(run_dir)
        return item_path

    # Settled before the folder and its lock are made: a request for a path
    # above real runs must not leave a lock there, which the sweep would take
    # for a run folder and delete the runs below with it.
    bundle_key = run_prefix + BUNDLE
    bundle_path = os.path.join(run_dir, BUNDLE)
    if not os.path.isfile(bundle_path) and not s3.object_exists(
        bundle_key,
        target=storage.target,
    ):
        return None

    with _run_lock(run_dir):
        _mark_used(run_dir)
        # Another request may have generated it while this one waited.
        if not _is_generated(run_dir, item, item_path):
            if not os.path.isfile(bundle_path) and not _download_bundle(
                storage,
                bundle_key,
                bundle_path,
            ):
                return None
            _generate(bundle_path, run_dir, item, item_path)
    return item_path if _is_generated(run_dir, item, item_path) else None


def _generate(bundle_path: str, run_dir: str, item: str, item_path: str) -> None:
    """Generate ``item`` in a scratch folder and move it into place once whole.

    A generation that fails, times out or dies with its worker thus leaves
    nothing a later request would take for a finished page.
    """
    for entry in os.scandir(run_dir):
        if entry.name.startswith(SCRATCH):
            shutil.rmtree(entry.path, ignore_errors=True)
    scratch = tempfile.mkdtemp(dir=run_dir, prefix=SCRATCH)
    try:
        scratch_item = os.path.join(scratch, *item.split('/'))
        os.makedirs(os.path.dirname(scratch_item), exist_ok=True)
        _run_generator(bundle_path, scratch_item)
        if item in GENERATED_DIRS:
            if not os.path.isdir(scratch_item):
                return
            # Pages made one by one before are superseded.
            shutil.rmtree(item_path, ignore_errors=True)
            os.replace(scratch_item, item_path)
            with open(os.path.join(run_dir, COMPLETE.format(item)), 'w'):
                pass
        else:
            # The page, and whatever the generator made beside it.
            os.makedirs(os.path.dirname(item_path), exist_ok=True)
            for entry in os.scandir(os.path.dirname(scratch_item)):
                target = os.path.join(os.path.dirname(item_path), entry.name)
                if entry.is_dir(follow_symlinks=False) and os.path.isdir(target):
                    shutil.rmtree(target, ignore_errors=True)
                os.replace(entry.path, target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def prune_cache(now: float | None = None, ttl: int | None = None) -> int:
    """Delete the cache folders of runs unused for ``ttl`` seconds; return how many.

    ``ttl`` defaults to ``LOGS_S3_CACHE_TTL``.

    A folder holds a run's downloaded bundle and the pages generated from it,
    all of which can be made again from S3. One in use (locked) is kept.
    """
    root = cache_root()
    if not os.path.isdir(root):
        return 0
    cutoff = (time.time() if now is None else now) - (cache_ttl() if ttl is None else ttl)
    pruned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        if LOCK not in filenames:
            continue
        # A run's folder: nothing below it is another run's.
        dirnames.clear()
        if _prune_run_dir(dirpath, cutoff):
            pruned += 1
            _remove_empty_parents(dirpath, root)
    return pruned


def _unused_since(lock_path: str, cutoff: float) -> bool:
    try:
        return os.stat(lock_path).st_mtime < cutoff
    except FileNotFoundError:
        return False


def _prune_run_dir(run_dir: str, cutoff: float) -> bool:
    lock_path = os.path.join(run_dir, LOCK)
    if not _unused_since(lock_path, cutoff):
        return False
    try:
        lock_file = open(lock_path, 'a')  # noqa: SIM115
    except FileNotFoundError:
        return False
    with lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        # Used, or pruned and made again, since the check above.
        if not _same_file(lock_file, lock_path) or not _unused_since(lock_path, cutoff):
            return False
        shutil.rmtree(run_dir, ignore_errors=True)
    return True


def _remove_empty_parents(path: str, root: str) -> None:
    root = os.path.normpath(root)
    parent = os.path.dirname(os.path.normpath(path))
    while parent != root and parent.startswith(root + os.sep):
        try:
            os.rmdir(parent)
        except OSError:
            return
        parent = os.path.dirname(parent)


def maybe_prune_cache() -> threading.Thread | None:
    """Start :func:`prune_cache` in the background; return the thread, if one was started.

    At most every ``PRUNE_INTERVAL`` seconds across processes, and one at a
    time in this one, so no request waits for a sweep.
    """
    root = cache_root()
    marker = os.path.join(root, PRUNED)
    now = time.time()
    try:
        if now - os.stat(marker).st_mtime < min(PRUNE_INTERVAL, max(cache_ttl(), 0)):
            return None
    except FileNotFoundError:
        pass
    if not _sweep.acquire(blocking=False):
        return None
    logger = get_task_or_server_logger()
    try:
        with open(marker, 'a'):
            pass
        os.utime(marker, (now, now))
    except OSError as error:
        _sweep.release()
        logger.warning(f'failed to prune {root}: {error}')
        return None

    def sweep():
        try:
            prune_cache(now)
        except OSError as error:
            logger.warning(f'failed to prune {root}: {error}')
        finally:
            _sweep.release()

    thread = threading.Thread(target=sweep, name='prune-logs-cache', daemon=True)
    thread.start()
    return thread


def _download_bundle(storage: S3LogsStorage, key: str, path: str) -> bool:
    """Download the bundle to ``path``; ``False`` if it is gone meanwhile."""
    fd, partial = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.part')
    os.close(fd)
    try:
        try:
            s3.download_file(key, partial, target=storage.target)
        except ClientError as exc:
            if s3.is_missing(exc):
                return False
            raise
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


def list_run_directory(storage: S3LogsStorage, prefix: str) -> tuple[list[str], list[str]]:
    """Sub-directories and files under ``prefix``, as names relative to it."""
    prefixes, keys = s3.list_entries(prefix, target=storage.target)
    directories = [p[len(prefix) :] for p in prefixes]
    files = [k[len(prefix) :] for k in keys if k != prefix]
    if BUNDLE in files:
        # Like a TE log server, offer the pages the bundle can produce.
        directories = sorted({*directories, *(f'{d}/' for d in GENERATED_DIRS)})
    return directories, files
