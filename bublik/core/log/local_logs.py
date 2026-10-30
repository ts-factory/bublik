# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Serving the logs of runs in a local logs base.

A local logs base is a directory Bublik owns, with no TE log server in front
of it (:mod:`bublik.core.logs_storage`). Bublik serves it the way that server
would: a file that exists is streamed, a missing ``json/`` or ``html/`` item
is generated from the run's ``raw_log_bundle.tpxz`` with ``rgt-log-get-item``
and then served. Unlike S3 runs, whose pages live in an expiring cache, the
pages are generated next to the bundle and stay, as on a log server. Only
paths under a configured base are served.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from bublik.core.log.s3_logs import (
    BUNDLE,
    GENERATED_DIRS,
    LogItem,
    _generate,
    _is_generated,
    _run_lock,
    content_type_of,
    split_generated,
)
from bublik.core.logs_storage import file_url, find_local_storage


if TYPE_CHECKING:
    from bublik.core.logs_storage import LocalLogsStorage


def storage_for(path: str) -> LocalLogsStorage | None:
    """The local logs base holding ``path``; only paths under one are served.

    A path with ``.`` or ``..`` segments is not, nor a relative one.
    """
    parts = path.split('/')
    if not path.startswith('/') or any(part in ('.', '..') for part in parts):
        return None
    return find_local_storage(file_url(path))


def open_local_item(storage: LocalLogsStorage, path: str) -> LogItem | None:
    """Open the file at ``path`` for streaming, generating a missing log page.

    ``None`` if it does not exist and cannot be generated. Raises
    :class:`~bublik.core.log.s3_logs.LogGenerationError` if generation was
    attempted and failed.
    """
    try:
        stream = open(path, 'rb')  # noqa: SIM115
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        stream = None
    if stream is not None:
        return LogItem(
            stream=stream,
            content_type=content_type_of(path),
            length=os.fstat(stream.fileno()).st_size,
        )

    split = split_generated(os.path.relpath(path, storage.root))
    # json/ and html/ themselves are listed (list_local_directory).
    if split is None or split[1] in GENERATED_DIRS:
        return None
    run_rel, item = split
    run_dir = os.path.normpath(os.path.join(storage.root, run_rel))
    top, _, below = item.partition('/')
    # Deeper than json/<page> or html/<page> (html/images/...): made only
    # with the whole directory.
    generated = generate_local_item(run_dir, top if '/' in below else item)
    if generated is None:
        return None
    try:
        stream = open(path, 'rb')  # noqa: SIM115
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return None
    return LogItem(
        stream=stream,
        content_type=content_type_of(path),
        length=os.fstat(stream.fileno()).st_size,
    )


def generate_local_item(run_dir: str, item: str) -> str | None:
    """Generate ``item`` of the run at ``run_dir`` in place; return its path.

    ``item`` is a file under ``json/`` or ``html/``, or one of those
    directories, generated whole. ``None`` if the run has no bundle.
    """
    item_path = os.path.normpath(os.path.join(run_dir, *item.split('/')))
    if not item_path.startswith(run_dir + os.sep):
        return None
    if _is_generated(run_dir, item, item_path):
        return item_path
    bundle_path = os.path.join(run_dir, BUNDLE)
    if not os.path.isfile(bundle_path):
        return None
    with _run_lock(run_dir):
        # Another request may have generated it while this one waited.
        if not _is_generated(run_dir, item, item_path):
            _generate(bundle_path, run_dir, item, item_path)
    return item_path if _is_generated(run_dir, item, item_path) else None


def list_local_directory(
    storage: LocalLogsStorage,
    path: str,
) -> tuple[list[str], list[str]] | None:
    """Sub-directories and files of the directory at ``path``; ``None`` if it is not one.

    ``<run>/json/`` and ``<run>/html/`` (and what is below them) are
    generated whole first when the run has a bundle, as a TE log server does.
    A run with a bundle is offered those directories even before they exist.
    Bublik's own markers (dot files) are not listed.
    """
    path = os.path.normpath(path)
    split = split_generated(os.path.relpath(path, storage.root))
    if split is not None:
        run_rel, item = split
        run_dir = os.path.normpath(os.path.join(storage.root, run_rel))
        if os.path.isfile(os.path.join(run_dir, BUNDLE)):
            generate_local_item(run_dir, item.partition('/')[0])
    try:
        entries = sorted(os.scandir(path), key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError):
        return None
    entries = [entry for entry in entries if not entry.name.startswith('.')]
    directories = [f'{entry.name}/' for entry in entries if entry.is_dir()]
    files = [entry.name for entry in entries if entry.is_file()]
    if BUNDLE in files:
        directories = sorted({*directories, *(f'{d}/' for d in GENERATED_DIRS)})
    return directories, files
