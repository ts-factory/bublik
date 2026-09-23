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
from typing import Protocol
from urllib.parse import urlsplit

from bublik.core.checks import check_run_file
from bublik.core.exceptions import ImportrunsError
from bublik.core.url import save_url_to_dir


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


def url_scheme(url: str) -> str:
    return urlsplit(url).scheme.lower()


def unsupported_source_error(url: str) -> ImportrunsError:
    return ImportrunsError(
        message=f'unsupported run source URL scheme: {url_scheme(url) or "none"}',
        debug_details=[f'Run source URL: {url}'],
    )


def run_source_for(url: str) -> RunSource:
    """The run source reading the run at ``url``."""
    if url_scheme(url) in ('http', 'https'):
        return HTTPRunSource(url)
    raise unsupported_source_error(url)
