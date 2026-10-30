# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Uploaded log tarballs.

Test Environment's ``publish_logs`` packs a run as a flat tar holding
``meta_data.json`` and ``raw_log_bundle.tpxz``. A TE log server unpacks such
tars with ``publish-logs-unpack.sh``; an upload goes through the same checks
and gets the same storage layout, ``TS_NAME/YYYY/MM/DD/CFG[-USER]-HH:MM:SS``,
under the project's upload base (:mod:`bublik.core.logs_storage`): a log
server's directory, or a directory or S3 prefix Bublik serves itself.

A tar holding ``bublik.json`` instead is a run made outside TE (a converted
Playwright report, say): its log pages are already made, under ``json/``, and
its attachments lie under ``attachments/``, so it is stored as it is and
served without generating anything.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import mimetypes
import re
import tarfile
from typing import IO, TYPE_CHECKING, Iterator

from bublik.core.exceptions import ImportrunsError
from bublik.core.logs_storage import DONE_MARKER


if TYPE_CHECKING:
    from bublik.core.logs_storage import LogsStorage, RunFile


META_DATA_JSON = 'meta_data.json'
META_VERSION = 1
MANDATORY_FILES = (META_DATA_JSON, 'raw_log_bundle.tpxz')
ALLOWED_FILES = (*MANDATORY_FILES, 'trc-brief.html', 'trc-stats.txt', DONE_MARKER)

BUBLIK_JSON = 'bublik.json'
BUNDLE_MANDATORY_FILES = (META_DATA_JSON, BUBLIK_JSON)
BUNDLE_TOP_LEVEL_FILES = (*BUNDLE_MANDATORY_FILES, DONE_MARKER)
# Where a bundle's pages and attachments lie; nothing else may be nested.
BUNDLE_DIRS = ('json', 'attachments')

# The same rules as test-environment's publish-logs-unpack.sh.
DATE_RE = re.compile(r'^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[1-2][0-9]|3[0-1])$')
NAME_RE = re.compile(r'^[-_:0-9a-zA-Z]{1,32}$')
# A bundle's names are not bound by the log server's limit: its CFG may be a
# preview's name, a DNS label of up to 63 characters.
BUNDLE_NAME_RE = re.compile(r'^[-_:0-9a-zA-Z]{1,64}$')
# START_TIMESTAMP is ISO 8601; the path keeps its HH:MM:SS.
TIME_RE = re.compile(
    r'^(?:.*T)?(?P<time>([0-1][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9])(?:$|[.+\-Z])'
)

# meta_data.json is a short list of metas; anything bigger is not one.
MAX_META_DATA_SIZE = 1024 * 1024

CONTENT_TYPES = {
    '.json': 'application/json',
    '.html': 'text/html; charset=utf-8',
    '.txt': 'text/plain; charset=utf-8',
    # A bundle's attachments, which a slim image's mimetypes may not know.
    '.png': 'image/png',
    '.webm': 'video/webm',
    '.zip': 'application/zip',
}


class UploadError(ImportrunsError):
    """An upload that cannot be accepted; ``status_code`` says why."""

    status_code = 400


class AlreadyUploadedError(UploadError):
    status_code = 409


class UploadTooLargeError(UploadError):
    status_code = 413


@dataclass
class LogTar:
    """A validated log tarball, open for reading its members."""

    tar: tarfile.TarFile
    members: list[tarfile.TarInfo]
    metas: dict[str, str]
    bundle: bool = False

    @property
    def subpath(self) -> str:
        return storage_subpath(self.metas, BUNDLE_NAME_RE if self.bundle else NAME_RE)

    @property
    def project_name(self) -> str | None:
        return self.metas.get('PROJECT') or None

    def files(self) -> Iterator[RunFile]:
        """The files to store, ``.done`` excepted: the storage writes its own last."""
        for member in self.members:
            if member.name != DONE_MARKER:
                yield member.name, self.tar.extractfile(member), _content_type(member.name)


def _invalid(message: str) -> UploadError:
    return UploadError(message=f'invalid log tarball: {message}')


def open_log_tar(fileobj: IO[bytes]) -> LogTar:
    """Open and validate an uploaded tarball, a TE one or a bundle.

    Raises :class:`UploadError` naming the first problem found.
    """
    try:
        tar = tarfile.open(fileobj=fileobj, mode='r:')  # noqa: SIM115
    except tarfile.TarError:
        msg = 'not an uncompressed tar archive (make it with publish_logs or "tar -cf")'
        raise _invalid(msg) from None

    members = tar.getmembers()
    bundle = any(member.name == BUBLIK_JSON for member in members)
    if bundle:
        members = _check_bundle_members(members)
    else:
        _check_te_members(members)

    metas = _read_metas(tar, tar.getmember(META_DATA_JSON))
    log_tar = LogTar(tar=tar, members=members, metas=metas, bundle=bundle)
    log_tar.subpath  # noqa: B018 -- validates the path-building metas
    return log_tar


def _check_te_members(members: list[tarfile.TarInfo]) -> None:
    names = [member.name for member in members]
    for member in members:
        if not member.isreg():
            msg = f'{member.name}: only regular files are allowed'
            raise _invalid(msg)
        if '/' in member.name or member.name in ('', '.', '..'):
            msg = f'{member.name}: files must be at the top level of the archive'
            raise _invalid(msg)
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        msg = f'duplicate files: {", ".join(duplicates)}'
        raise _invalid(msg)
    not_allowed = [name for name in names if name not in ALLOWED_FILES]
    if not_allowed:
        msg = (
            f'files not allowed: {", ".join(not_allowed)} (allowed: {", ".join(ALLOWED_FILES)})'
        )
        raise _invalid(msg)
    missing = [name for name in MANDATORY_FILES if name not in names]
    if missing:
        msg = f'missing mandatory files: {", ".join(missing)}'
        raise _invalid(msg)


def _check_bundle_members(members: list[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    """Check a bundle's members; return its files (directory entries dropped)."""
    files = []
    for member in members:
        parts = member.name.split('/')
        if '\\' in member.name or any(part in ('', '.', '..') for part in parts):
            msg = f'{member.name}: not a plain relative path'
            raise _invalid(msg)
        if member.isdir():
            if parts[0] not in BUNDLE_DIRS:
                msg = f'{member.name}: only {", ".join(BUNDLE_DIRS)} directories are allowed'
                raise _invalid(msg)
            continue
        if not member.isreg():
            msg = f'{member.name}: only regular files and directories are allowed'
            raise _invalid(msg)
        if len(parts) == 1 and member.name not in BUNDLE_TOP_LEVEL_FILES:
            msg = (
                f'{member.name}: files not allowed at the top level '
                f'(allowed: {", ".join(BUNDLE_TOP_LEVEL_FILES)})'
            )
            raise _invalid(msg)
        if len(parts) > 1 and parts[0] not in BUNDLE_DIRS:
            msg = f'{member.name}: files must lie under {" or ".join(BUNDLE_DIRS)}'
            raise _invalid(msg)
        files.append(member)

    names = [member.name for member in files]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        msg = f'duplicate files: {", ".join(duplicates)}'
        raise _invalid(msg)
    missing = [name for name in BUNDLE_MANDATORY_FILES if name not in names]
    if missing:
        msg = f'missing mandatory files: {", ".join(missing)}'
        raise _invalid(msg)
    return files


def _read_metas(tar: tarfile.TarFile, member: tarfile.TarInfo) -> dict[str, str]:
    if member.size > MAX_META_DATA_SIZE:
        msg = f'{META_DATA_JSON} is larger than {MAX_META_DATA_SIZE} bytes'
        raise _invalid(msg)
    try:
        meta_data = json.load(tar.extractfile(member))
    except (ValueError, UnicodeDecodeError):
        msg = f'{META_DATA_JSON} is not valid JSON'
        raise _invalid(msg) from None
    if not isinstance(meta_data, dict):
        msg = f'{META_DATA_JSON} must be a JSON object'
        raise _invalid(msg)
    if meta_data.get('version') != META_VERSION:
        msg = f'unsupported {META_DATA_JSON} version: {meta_data.get("version")!r}'
        raise _invalid(msg)

    metas: dict[str, str] = {}
    for meta in meta_data.get('metas') or []:
        if isinstance(meta, dict) and isinstance(meta.get('name'), str):
            metas.setdefault(meta['name'], str(meta.get('value', '')))
    return metas


def _mandatory_meta(metas: dict[str, str], name: str, pattern: re.Pattern | None = None):
    value = metas.get(name, '')
    if not value:
        msg = f'missing meta {name}'
        raise _invalid(msg)
    if pattern is not None and not pattern.fullmatch(value):
        msg = f'invalid meta {name}: {value!r}'
        raise _invalid(msg)
    return value


def storage_subpath(metas: dict[str, str], name_re: re.Pattern = NAME_RE) -> str:
    """The run's path under a logs base: ``TS_NAME/YYYY/MM/DD/CFG[-USER]-HH:MM:SS``."""
    campaign_date = _mandatory_meta(metas, 'CAMPAIGN_DATE', DATE_RE)
    ts_name = _mandatory_meta(metas, 'TS_NAME', name_re)
    cfg = _mandatory_meta(metas, 'CFG', name_re)
    timestamp = _mandatory_meta(metas, 'START_TIMESTAMP')

    user = metas.get('USER', '')
    if user and not name_re.fullmatch(user):
        msg = f'invalid meta USER: {user!r}'
        raise _invalid(msg)

    time_match = TIME_RE.match(timestamp)
    if time_match is None:
        msg = f'unexpected START_TIMESTAMP format: {timestamp!r}'
        raise _invalid(msg)

    user_part = f'-{user}' if user else ''
    return (
        f'{ts_name}/{campaign_date.replace("-", "/")}/'
        f'{cfg}{user_part}-{time_match.group("time")}'
    )


def publish_log_tar(log_tar: LogTar, storage: LogsStorage) -> str:
    """Store the tarball's files under the upload base; return the run's URL.

    ``.done`` goes last, once everything else is in place, as the log
    server's unpack script does; a run without it is an upload that failed
    midway, and is written over.
    """
    subpath = log_tar.subpath
    url = storage.run_url(subpath)
    if storage.is_published(subpath):
        msg = (
            f'these logs are already uploaded to {url}; to import them again, '
            f'use importruns/source/?url={url}&force=true'
        )
        raise AlreadyUploadedError(message=msg)
    storage.store_run(subpath, log_tar.files())
    return url


def _content_type(name: str) -> str:
    for suffix, content_type in CONTENT_TYPES.items():
        if name.endswith(suffix):
            return content_type
    content_type, _ = mimetypes.guess_type(name)
    return content_type or 'application/octet-stream'
