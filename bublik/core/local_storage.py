# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Local-filesystem storage backend for AI-chat generated files.

Used when ``AI_CHAT_FILE_STORAGE_BACKEND`` is ``local`` (see
:mod:`bublik.core.ai_chat_storage`). Object keys are reused verbatim as paths
relative to ``AI_CHAT_FILE_STORAGE_DIR``, so the on-disk layout mirrors the
bucket layout: ``chat/<thread>/<file>/<name>``.

Generated files are private to one user, and unlike an S3 bucket this tree
lives on a filesystem shared with everything else on the host: directories are
created ``0700`` and files ``0600``.

All functions are synchronous; async callers must run them in a thread
(``anyio.to_thread.run_sync`` / ``sync_to_async``).
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

from django.core.exceptions import ImproperlyConfigured

from bublik.core.ai_chat_storage import setting


_DIR_MODE = 0o700
_FILE_MODE = 0o600


def root() -> Path:
    """The configured storage root, or raise if the deployment never set one.

    Deliberately has no default: writing generated files to an arbitrary
    fallback directory (the source tree, the CWD) is worse than failing with
    an actionable message at the first upload.
    """
    configured = setting('AI_CHAT_FILE_STORAGE_DIR')
    if not configured:
        msg = (
            'The "local" AI chat file storage backend is selected, but '
            'AI_CHAT_FILE_STORAGE_DIR is not set, so generated files have '
            'nowhere to go. Set AI_CHAT_FILE_STORAGE_DIR to a writable '
            'directory, or select the "s3" backend with '
            'AI_CHAT_FILE_STORAGE_BACKEND.'
        )
        raise ImproperlyConfigured(msg)
    return Path(configured).resolve()


def _resolve(key: str) -> Path:
    """Map an object key to an absolute path, refusing to escape the root.

    ``sanitize_filename`` already strips separators from the user-influenced
    part of the key, but here the key becomes a filesystem path, so the
    containment check is enforced at the boundary regardless.
    """
    base = root()
    candidate = (base / key).resolve()
    if candidate != base and base not in candidate.parents:
        msg = f'Refusing to access {key!r} outside the chat file storage root.'
        raise ValueError(msg)
    return candidate


def _ensure_dir(directory: Path) -> None:
    """Create ``directory`` and any missing parents with private permissions.

    ``mkdir(parents=True, mode=...)`` applies the mode to the leaf only, and
    even there it is masked by the process umask, so the directories we
    actually create are chmod'ed explicitly. Pre-existing directories are left
    alone: their permissions are the operator's choice.
    """
    created = []
    current = directory
    base = root()
    while not current.exists():
        created.append(current)
        if current == base:
            break
        current = current.parent
    directory.mkdir(parents=True, exist_ok=True)
    for path in created:
        os.chmod(path, _DIR_MODE)


def upload_bytes(key: str, data: bytes, content_type: str) -> None:
    """Write one object atomically (content type is not stored; the DB has it)."""
    del content_type
    path = _resolve(key)
    _ensure_dir(path.parent)
    # Write-then-rename so a reader never observes a half-written file, and a
    # crash mid-write leaves no partial object behind under the real key.
    # mkstemp already creates the file 0600; the chmod makes that explicit.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f'.{path.name}.')
    try:
        with os.fdopen(fd, 'wb') as tmp:
            tmp.write(data)
        os.chmod(tmp_name, _FILE_MODE)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_object(key: str) -> bytes:
    """Read a whole object into memory (objects are capped by AI_CHAT_FILE_MAX_SIZE)."""
    return _resolve(key).read_bytes()


def delete_prefix(prefix: str) -> None:
    """Best-effort recursive delete of everything under ``prefix``."""
    target = _resolve(prefix)
    base = root()
    if target == base:
        # A prefix that resolves to the root would wipe every thread's files.
        msg = 'Refusing to delete the whole chat file storage root.'
        raise ValueError(msg)
    shutil.rmtree(target, ignore_errors=True)
    # Drop now-empty parents (the per-thread directory, then `chat/`) so the
    # tree does not accumulate empty skeletons for every deleted thread.
    for parent in target.parents:
        if parent == base:
            break
        try:
            parent.rmdir()
        except OSError:
            break


def public_download_url(key: str, filename: str) -> None:
    """Never redirect: local files are always proxied by the download endpoint."""
    del key, filename
