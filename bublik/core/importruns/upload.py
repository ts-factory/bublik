# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Uploaded TE log tarballs.

Test Environment's ``publish_logs`` packs a run as a flat tar holding
``meta_data.json`` and ``raw_log_bundle.tpxz``. A TE log server unpacks such
tars with ``publish-logs-unpack.sh``; an upload goes through the same checks
and gets the same storage layout, ``TS_NAME/YYYY/MM/DD/CFG[-USER]-HH:MM:SS``,
but lands under the project's S3 upload base instead of the server's disk.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import tarfile
from typing import IO, TYPE_CHECKING

from bublik.core import s3
from bublik.core.exceptions import ImportrunsError


if TYPE_CHECKING:
    from bublik.core.logs_storage import LogsStorage


META_DATA_JSON = 'meta_data.json'
DONE_MARKER = '.done'
META_VERSION = 1
MANDATORY_FILES = (META_DATA_JSON, 'raw_log_bundle.tpxz')
ALLOWED_FILES = (*MANDATORY_FILES, 'trc-brief.html', 'trc-stats.txt', DONE_MARKER)

# The same rules as test-environment's publish-logs-unpack.sh.
DATE_RE = re.compile(r'^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[1-2][0-9]|3[0-1])$')
NAME_RE = re.compile(r'^[-_:0-9a-zA-Z]{1,32}$')
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
    """A validated TE log tarball, open for reading its members."""

    tar: tarfile.TarFile
    members: list[tarfile.TarInfo]
    metas: dict[str, str]

    @property
    def subpath(self) -> str:
        return storage_subpath(self.metas)

    @property
    def project_name(self) -> str | None:
        return self.metas.get('PROJECT') or None


def _invalid(message: str) -> UploadError:
    return UploadError(message=f'invalid log tarball: {message}')


def open_log_tar(fileobj: IO[bytes]) -> LogTar:
    """Open and validate an uploaded tarball.

    Raises :class:`UploadError` naming the first problem found.
    """
    try:
        tar = tarfile.open(fileobj=fileobj, mode='r:')  # noqa: SIM115
    except tarfile.TarError:
        msg = 'not an uncompressed tar archive (make it with publish_logs or "tar -cf")'
        raise _invalid(msg) from None

    members = tar.getmembers()
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

    metas = _read_metas(tar, tar.getmember(META_DATA_JSON))
    log_tar = LogTar(tar=tar, members=members, metas=metas)
    storage_subpath(metas)  # validates the path-building metas
    return log_tar


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


def storage_subpath(metas: dict[str, str]) -> str:
    """The run's path under a logs base: ``TS_NAME/YYYY/MM/DD/CFG[-USER]-HH:MM:SS``."""
    campaign_date = _mandatory_meta(metas, 'CAMPAIGN_DATE', DATE_RE)
    ts_name = _mandatory_meta(metas, 'TS_NAME', NAME_RE)
    cfg = _mandatory_meta(metas, 'CFG', NAME_RE)
    timestamp = _mandatory_meta(metas, 'START_TIMESTAMP')

    user = metas.get('USER', '')
    if user and not NAME_RE.match(user):
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


def run_prefix(storage: LogsStorage, subpath: str) -> str:
    """The object key prefix a run with ``subpath`` is stored under."""
    base_key = storage.key(storage.uri)
    if base_key and not base_key.endswith('/'):
        base_key += '/'
    return f'{base_key}{subpath}/'


def publish_log_tar(log_tar: LogTar, storage: LogsStorage) -> str:
    """Store the tarball's files under the upload base; return the run's ``s3://`` URL.

    ``.done`` goes last, once everything else is in place, as the log
    server's unpack script does; a prefix without it is an upload that failed
    midway, and is written over.
    """
    target = storage.target
    prefix = run_prefix(storage, log_tar.subpath)
    url = storage.url(prefix)
    if s3.object_exists(prefix + DONE_MARKER, target=target):
        msg = (
            f'these logs are already uploaded to {url}; to import them again, '
            f'use importruns/source/?url={url}&force=true'
        )
        raise AlreadyUploadedError(message=msg)

    for member in log_tar.members:
        if member.name == DONE_MARKER:
            continue
        s3.upload_fileobj(
            prefix + member.name,
            log_tar.tar.extractfile(member),
            _content_type(member.name),
            target=target,
        )
    s3.upload_bytes(prefix + DONE_MARKER, b'', 'application/octet-stream', target=target)
    return url


def _content_type(name: str) -> str:
    for suffix, content_type in CONTENT_TYPES.items():
        if name.endswith(suffix):
            return content_type
    return 'application/octet-stream'
