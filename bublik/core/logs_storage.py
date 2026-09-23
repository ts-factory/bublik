# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Logs bases stored in S3.

A ``LOGS_BASES`` entry of the references config normally names the HTTP URIs
of a TE log server. An entry with an S3 ``storage`` block instead names
``s3://bucket/prefix/`` URIs, and says how to reach that bucket::

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
``{"type": "s3"}``. ``upload: true`` marks the base that tarball uploads for
the project go to.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from bublik.core import s3
from bublik.core.config.services import ConfigServices
from bublik.core.exceptions import ImportrunsError
from bublik.core.secrets import resolve_secret_reference
from bublik.data.models import GlobalConfigs, Project


S3_SCHEME = 's3'
LOGS_SECRET_PREFIX = 'LOGS_'


class LogsStorageError(ImportrunsError):
    """An S3 logs base is misconfigured or cannot be used."""


@dataclass(frozen=True)
class LogsStorage:
    """An S3-backed logs base: its config entry and how to reach its bucket."""

    base: dict
    target: s3.S3Target

    @property
    def uri(self) -> str:
        """The base URI runs are recorded under (the one references store)."""
        return self.base['uri'][-1]

    @property
    def upload(self) -> bool:
        return is_upload_base(self.base)

    def key(self, url: str) -> str:
        """The object key an ``s3://`` URL of this bucket points at."""
        return parse_s3_url(url)[1]

    def url(self, key: str) -> str:
        return f'{S3_SCHEME}://{self.target.bucket}/{key}'


def is_s3_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() == S3_SCHEME


def parse_s3_url(url: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``."""
    parts = urlsplit(url)
    if parts.scheme.lower() != S3_SCHEME or not parts.netloc:
        msg = f'not an s3://bucket/key URL: {url}'
        raise LogsStorageError(message=msg)
    return parts.netloc, parts.path.lstrip('/')


def is_s3_base(base: dict) -> bool:
    return (base.get('storage') or {}).get('type') == S3_SCHEME


def is_upload_base(base: dict) -> bool:
    """Whether tarball uploads go to this base."""
    return is_s3_base(base) and bool(base['storage'].get('upload'))


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


def storage_for_base(base: dict) -> LogsStorage:
    """Build the storage of an S3 logs base, resolving its credentials."""
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
    return LogsStorage(base=base, target=target)


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


def find_s3_storage(url: str, project_id: int | None = None) -> LogsStorage | None:
    """The S3 logs base whose URI ``url`` starts with.

    Looks in the project's references, or in every project's when none is
    given -- the same scope the import API validates URLs against.
    """
    for pid in _candidate_project_ids(project_id):
        for base in logs_bases(pid):
            if is_s3_base(base) and any(url.startswith(uri) for uri in base['uri']):
                return storage_for_base(base)
    return None


def upload_storage_for(project_id: int) -> LogsStorage | None:
    """The S3 logs base tarball uploads for the project go to, if any."""
    for base in logs_bases(project_id):
        if is_upload_base(base):
            return storage_for_base(base)
    return None
