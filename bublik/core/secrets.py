# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Secret references in configs.

Configs are readable through the config API, so a secret is never stored in
one. A config holds a strict, source-qualified reference instead --
``${env:NAME}`` reads only :data:`os.environ`, ``${settings:NAME}`` reads only
:mod:`django.conf.settings` -- and each consumer restricts ``NAME`` to its own
prefix (``AI_`` for the ai config, ``LOGS_`` for logs storage). Without the
prefix a config author could point a reference at any setting (``SECRET_KEY``,
database credentials, ...) and send it to an endpoint they also control.
"""

from __future__ import annotations

import functools
import os
import re

from django.conf import settings


@functools.cache
def secret_reference_re(prefix: str) -> re.Pattern[str]:
    """Return the pattern matching one reference to a ``prefix`` name."""
    if not re.fullmatch(r'[A-Z][A-Z0-9]*_', prefix):
        msg = f'Invalid secret name prefix: {prefix!r}'
        raise ValueError(msg)
    return re.compile(
        rf'^\$\{{(?P<source>env|settings):(?P<name>{prefix}[A-Z0-9_]+)\}}$',
    )


def resolve_secret_reference(reference: str | None, prefix: str) -> str | None:
    """Resolve one strict, source-qualified secret reference.

    Only names starting with ``prefix`` are dereferenced. Invalid references
    and missing, empty, or non-string values are unresolved and return ``None``.
    """
    if not reference:
        return None
    match = secret_reference_re(prefix).fullmatch(reference)
    if match is None:
        return None
    name = match.group('name')
    if match.group('source') == 'env':
        value = os.environ.get(name)
    else:
        value = getattr(settings, name, None)
    return value if isinstance(value, str) and value else None
