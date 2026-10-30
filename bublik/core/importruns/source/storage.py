# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Where the files of a run being imported come from.

A run source is addressed by the URL the import was scheduled with. The import
only ever asks it two things: copy a named file into the processing directory,
and whether a named file exists. :func:`run_source_for` picks the
implementation from the URL scheme.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

from botocore.exceptions import ClientError

from bublik.core import s3
from bublik.core.checks import check_run_file
from bublik.core.exceptions import ImportrunsError
from bublik.core.logs_storage import LogsStorageError, find_s3_storage, is_s3_url
from bublik.core.url import save_url_to_dir


if TYPE_CHECKING:
    from bublik.core.logs_storage import S3LogsStorage


class RunSource(Protocol):
    url: str

    def fetch_to_dir(self, name: str, save_dir: str) -> bool:
        """Copy ``name`` into ``save_dir``; ``False`` (leaving nothing) if absent."""

    def exists(self, name: str, logger=None) -> bool:
        """Whether ``name`` exists in the run."""

    def location(self, name: str) -> str:
        """Human-readable location of ``name``, for logs."""


class HTTPRunSource:
    """A run directory served by a TE log server (Apache directory listing)."""

    def __init__(self, url: str):
        self.url = url

    def fetch_to_dir(self, name: str, save_dir: str) -> bool:
        return save_url_to_dir(self.url, save_dir, name)

    def exists(self, name: str, logger=None) -> bool:
        return check_run_file(name, self.url, logger)

    def location(self, name: str) -> str:
        return os.path.join(self.url, name)


class S3RunSource:
    """A run directory under an S3 logs base (``s3://bucket/prefix/run/``)."""

    def __init__(self, url: str, storage: S3LogsStorage):
        self.url = url if url.endswith('/') else url + '/'
        self.storage = storage
        self.prefix = storage.key(self.url)

    def fetch_to_dir(self, name: str, save_dir: str) -> bool:
        path = os.path.join(save_dir, name)
        try:
            s3.download_file(self.prefix + name, path, target=self.storage.target)
        except ClientError as exc:
            if os.path.exists(path):
                os.unlink(path)
            if s3.is_missing(exc):
                return False
            raise
        return True

    def exists(self, name: str, logger=None) -> bool:
        found = s3.object_exists(self.prefix + name, target=self.storage.target)
        if not found and logger:
            logger.info(f"{name} wasn't found in {self.url}")
        return found

    def location(self, name: str) -> str:
        return self.url + name


def s3_storage_for_url(url: str, project_id: int | None = None) -> S3LogsStorage:
    """The S3 logs base ``url`` belongs to, or an import error if none is configured."""
    storage = find_s3_storage(url, project_id)
    if storage is None:
        msg = (
            "run URL doesn't match any S3 logs base in the references "
            'configuration (a LOGS_BASES entry with storage.type "s3")'
        )
        raise LogsStorageError(message=msg, debug_details=[f'Run source URL: {url}'])
    return storage


def url_scheme(url: str) -> str:
    return urlsplit(url).scheme.lower()


def unsupported_source_error(url: str) -> ImportrunsError:
    return ImportrunsError(
        message=f'unsupported run source URL scheme: {url_scheme(url) or "none"}',
        debug_details=[f'Run source URL: {url}'],
    )


def run_source_for(url: str, project_id: int | None = None) -> RunSource:
    """The run source reading the run at ``url``."""
    if url_scheme(url) in ('http', 'https'):
        return HTTPRunSource(url)
    if is_s3_url(url):
        return S3RunSource(url, s3_storage_for_url(url, project_id))
    raise unsupported_source_error(url)
