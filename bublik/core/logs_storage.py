# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Logs bases Bublik can write runs to.

A ``LOGS_BASES`` entry of the references config normally names the HTTP URIs
of a TE log server and nothing else. An entry with a ``storage`` block also
says where the files behind those URIs are and how Bublik reaches them, so
that uploaded tarballs can be stored there.

An S3 storage names ``s3://bucket/prefix/`` URIs, and says how to reach that
bucket::

    {"name": "Uploads", "uri": ["s3://bublik-logs/uploads/"],
     "storage": {"type": "s3",
                 "endpoint_url": "http://127.0.0.1:8333",
                 "public_endpoint_url": "https://s3.example.com",
                 "region": "us-east-1",
                 "access_key": "${env:LOGS_S3_ACCESS_KEY}",
                 "secret_key": "${env:LOGS_S3_SECRET_KEY}",
                 "upload": true}}

Configs are readable through the config API, so credentials are never stored
in one: they are ``LOGS_``-prefixed secret references (see
:mod:`bublik.core.secrets`). Every field but ``type`` is optional and falls
back to the matching ``S3_*`` setting, so the bundled SeaweedFS needs only
``{"type": "s3"}``. Runs stored in S3 are served by Bublik itself
(:mod:`bublik.core.log.s3_logs`).

A filesystem storage keeps the HTTP URIs of a TE log server and names the
directory that server serves them from, as Bublik sees it::

    {"name": "Test Logs Base", "uri": ["https://bublik.example.com/logs/"],
     "storage": {"type": "fs", "root": "/home/te-logs/logs", "upload": true}}

Uploads then land next to the runs the server's ``publish-logs-unpack.sh``
unpacks, in the same layout, and are imported and served through the server
like any other run. The directory must be writable by Bublik and readable by
the log server: the same host, a shared mount, or in Docker the log server's
volume mounted into the Django container.

A local storage is a directory Bublik owns and serves itself, for a host
with neither a TE log server nor S3. Its URIs are ``file://`` URLs of that
directory::

    {"name": "Logs", "uri": ["file:///srv/bublik/logs/"],
     "storage": {"type": "local", "upload": true}}

Uploads are unpacked there in the log server's layout, imported by the
worker from the same path (so Bublik and its workers share the directory),
and served on Bublik's own route with pages generated from the bundle on
request, as a TE log server does (:mod:`bublik.core.log.local_logs`).

``upload: true`` marks the base that tarball uploads for the project go to.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import stat
from typing import IO, Iterable
from urllib.parse import quote, unquote, urlsplit

from bublik.core import s3
from bublik.core.config.services import ConfigServices
from bublik.core.exceptions import ImportrunsError
from bublik.core.secrets import resolve_secret_reference
from bublik.data.models import GlobalConfigs, Project


S3_SCHEME = 's3'
FILE_SCHEME = 'file'
STORAGE_S3 = 's3'
STORAGE_FS = 'fs'
STORAGE_LOCAL = 'local'
LOGS_SECRET_PREFIX = 'LOGS_'
# Written last into a run's directory: everything before it is in place. The
# same marker publish-logs-unpack.sh leaves.
DONE_MARKER = '.done'

# A file to store: its path within the run, its contents and content type.
RunFile = tuple[str, IO[bytes], str]


class LogsStorageError(ImportrunsError):
    """A logs base's storage is misconfigured or cannot be used."""


class LogsStorage:
    """A logs base Bublik can store runs in; see the subclasses."""

    base: dict

    @property
    def name(self) -> str:
        return self.base['name']

    @property
    def uri(self) -> str:
        """The base URI runs are recorded under (the one references store)."""
        return self.base['uri'][-1]

    @property
    def upload(self) -> bool:
        return is_upload_base(self.base)

    def run_url(self, subpath: str) -> str:
        """The URL the run at ``subpath`` under this base is imported from."""
        raise NotImplementedError

    def is_published(self, subpath: str) -> bool:
        """Whether a run at ``subpath`` is stored whole (has its ``.done``)."""
        raise NotImplementedError

    def store_run(self, subpath: str, files: Iterable[RunFile]) -> None:
        """Store a run's files at ``subpath``, then its ``.done`` marker."""
        raise NotImplementedError


@dataclass(frozen=True)
class S3LogsStorage(LogsStorage):
    """An S3-backed logs base: its config entry and how to reach its bucket."""

    base: dict
    target: s3.S3Target

    def key(self, url: str) -> str:
        """The object key an ``s3://`` URL of this bucket points at."""
        return parse_s3_url(url)[1]

    def url(self, key: str) -> str:
        return f'{S3_SCHEME}://{self.target.bucket}/{key}'

    def run_prefix(self, subpath: str) -> str:
        """The object key prefix a run with ``subpath`` is stored under."""
        base_key = self.key(self.uri)
        if base_key and not base_key.endswith('/'):
            base_key += '/'
        return f'{base_key}{subpath}/'

    def run_url(self, subpath: str) -> str:
        return self.url(self.run_prefix(subpath))

    def is_published(self, subpath: str) -> bool:
        return s3.object_exists(self.run_prefix(subpath) + DONE_MARKER, target=self.target)

    def store_run(self, subpath: str, files: Iterable[RunFile]) -> None:
        prefix = self.run_prefix(subpath)
        for name, fileobj, content_type in files:
            s3.upload_fileobj(prefix + name, fileobj, content_type, target=self.target)
        s3.upload_bytes(
            prefix + DONE_MARKER,
            b'',
            'application/octet-stream',
            target=self.target,
        )


@dataclass(frozen=True)
class DirectoryLogsStorage(LogsStorage):
    """A logs base that is a directory Bublik writes runs into."""

    base: dict
    root: str

    def run_dir(self, subpath: str) -> str:
        return os.path.join(self.root, subpath)

    def is_published(self, subpath: str) -> bool:
        return os.path.isfile(os.path.join(self.run_dir(subpath), DONE_MARKER))

    def store_run(self, subpath: str, files: Iterable[RunFile]) -> None:
        """Unpack the run as ``publish-logs-unpack.sh`` would.

        The log server generates a run's ``json/`` and ``html/`` pages into
        the run's directory on request, running as the web server's user, so
        the directory is made writable by its group, as the unpack script
        makes it: with the logs directory group-owned by the web server and
        setgid, the group is inherited. Everything else keeps Bublik's umask.

        A run directory left without ``.done`` is an upload that failed
        midway; it is replaced rather than merged into.
        """
        if not os.path.isdir(self.root):
            msg = (
                f'logs base {self.name!r}: storage root {self.root!r} is not a directory '
                'Bublik can see'
            )
            raise LogsStorageError(message=msg)
        run_dir = self.run_dir(subpath)
        if os.path.isdir(run_dir):
            shutil.rmtree(run_dir)
        os.makedirs(os.path.dirname(run_dir), exist_ok=True)
        os.mkdir(run_dir)
        os.chmod(run_dir, stat.S_IMODE(os.stat(run_dir).st_mode) | stat.S_IRWXG)
        for name, fileobj, _content_type in files:
            path = os.path.join(run_dir, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as target:
                shutil.copyfileobj(fileobj, target)
        open(os.path.join(run_dir, DONE_MARKER), 'wb').close()


class FsLogsStorage(DirectoryLogsStorage):
    """A TE log server's directory, served at the base's HTTP URIs."""

    def run_url(self, subpath: str) -> str:
        return f'{self.uri.rstrip("/")}/{subpath}/'


class LocalLogsStorage(DirectoryLogsStorage):
    """A directory Bublik serves itself, at ``file://`` URLs."""

    def run_url(self, subpath: str) -> str:
        return file_url(self.run_dir(subpath)) + '/'

    def path_of(self, url: str) -> str:
        """The path a ``file://`` URL under this base points at."""
        return parse_file_url(url)


def is_s3_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() == S3_SCHEME


def is_local_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() == FILE_SCHEME


def file_url(path: str) -> str:
    """The ``file://`` URL of an absolute path; ``:`` stays readable, as in run names."""
    return f'{FILE_SCHEME}://{quote(path, safe="/:")}'


def parse_file_url(url: str) -> str:
    """The absolute, normalized path a ``file:///path`` URL names."""
    parts = urlsplit(url)
    if (
        parts.scheme.lower() != FILE_SCHEME
        or parts.netloc not in ('', 'localhost')
        or not parts.path.startswith('/')
    ):
        msg = f'not a file:///path URL: {url}'
        raise LogsStorageError(message=msg)
    return os.path.normpath(unquote(parts.path))


def parse_s3_url(url: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``."""
    parts = urlsplit(url)
    if parts.scheme.lower() != S3_SCHEME or not parts.netloc:
        msg = f'not an s3://bucket/key URL: {url}'
        raise LogsStorageError(message=msg)
    return parts.netloc, parts.path.lstrip('/')


def storage_type(base: dict) -> str | None:
    return (base.get('storage') or {}).get('type')


def is_s3_base(base: dict) -> bool:
    return storage_type(base) == STORAGE_S3


def is_fs_base(base: dict) -> bool:
    return storage_type(base) == STORAGE_FS


def is_local_base(base: dict) -> bool:
    return storage_type(base) == STORAGE_LOCAL


def is_upload_base(base: dict) -> bool:
    """Whether tarball uploads go to this base."""
    return storage_type(base) is not None and bool(base['storage'].get('upload'))


def _secret(storage: dict, field: str, setting_name: str, base_name: str) -> str:
    reference = storage.get(field)
    if reference is None:
        return s3.setting(setting_name)
    value = resolve_secret_reference(reference, LOGS_SECRET_PREFIX)
    if value is None:
        msg = (
            f'logs base {base_name!r}: {field} {reference!r} does not resolve; '
            f'it must be ${{env:{LOGS_SECRET_PREFIX}NAME}} or '
            f'${{settings:{LOGS_SECRET_PREFIX}NAME}} naming a non-empty value'
        )
        raise LogsStorageError(message=msg)
    return value


def _s3_storage_for_base(base: dict) -> S3LogsStorage:
    storage = base['storage']
    bucket, _ = parse_s3_url(base['uri'][-1])
    target = s3.S3Target(
        bucket=bucket,
        endpoint_url=storage.get('endpoint_url') or s3.setting('S3_ENDPOINT_URL'),
        access_key=_secret(storage, 'access_key', 'S3_ACCESS_KEY', base['name']),
        secret_key=_secret(storage, 'secret_key', 'S3_SECRET_KEY', base['name']),
        region=storage.get('region') or s3.setting('S3_REGION'),
        public_endpoint_url=(
            storage.get('public_endpoint_url') or s3.setting('S3_PUBLIC_ENDPOINT_URL')
        ),
    )
    return S3LogsStorage(base=base, target=target)


def _fs_storage_for_base(base: dict) -> FsLogsStorage:
    root = base['storage'].get('root')
    if not root or not os.path.isabs(root):
        msg = f'logs base {base["name"]!r}: storage root must be an absolute path, not {root!r}'
        raise LogsStorageError(message=msg)
    return FsLogsStorage(base=base, root=os.path.normpath(root))


def _local_storage_for_base(base: dict) -> LocalLogsStorage:
    return LocalLogsStorage(base=base, root=parse_file_url(base['uri'][-1]))


def storage_for_base(base: dict) -> LogsStorage:
    """Build the storage of a logs base with a ``storage`` block."""
    kind = storage_type(base)
    if kind == STORAGE_S3:
        return _s3_storage_for_base(base)
    if kind == STORAGE_FS:
        return _fs_storage_for_base(base)
    if kind == STORAGE_LOCAL:
        return _local_storage_for_base(base)
    msg = f'logs base {base["name"]!r}: unknown storage type {kind!r}'
    raise LogsStorageError(message=msg)


def logs_bases(project_id: int | None) -> list[dict]:
    return ConfigServices.getattr_from_global(
        GlobalConfigs.REFERENCES.name,
        'LOGS_BASES',
        project_id,
    )


def _candidate_project_ids(project_id: int | None) -> list[int | None]:
    if project_id is not None:
        return [project_id]
    return [*Project.objects.values_list('id', flat=True), None]


def find_s3_storage(url: str, project_id: int | None = None) -> S3LogsStorage | None:
    """The S3 logs base whose URI ``url`` starts with.

    Looks in the project's references, or in every project's when none is
    given -- the same scope the import API validates URLs against.
    """
    for pid in _candidate_project_ids(project_id):
        for base in logs_bases(pid):
            if is_s3_base(base) and any(url.startswith(uri) for uri in base['uri']):
                return _s3_storage_for_base(base)
    return None


def find_local_storage(url: str, project_id: int | None = None) -> LocalLogsStorage | None:
    """The local logs base whose directory holds the path ``url`` names.

    Compared as paths, not as strings, so ``..`` segments cannot escape a base.
    """
    try:
        path = parse_file_url(url)
    except LogsStorageError:
        return None
    for pid in _candidate_project_ids(project_id):
        for base in logs_bases(pid):
            if not is_local_base(base):
                continue
            storage = _local_storage_for_base(base)
            if path == storage.root or path.startswith(storage.root.rstrip('/') + '/'):
                return storage
    return None


def upload_storage_for(project_id: int) -> LogsStorage | None:
    """The logs base tarball uploads for the project go to, if any."""
    for base in logs_bases(project_id):
        if is_upload_base(base):
            return storage_for_base(base)
    return None
