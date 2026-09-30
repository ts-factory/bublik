# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from functools import wraps
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Generator


class ImportStats:
    """Counters and accumulated durations collected during a single import."""

    def __init__(self) -> None:
        self.counts: Counter = Counter()
        self._durations: defaultdict[str, float] = defaultdict(float)

    def incr(self, key: str, n: int = 1):
        self.counts[key] += n

    def duration(self, key: str) -> timedelta:
        return timedelta(seconds=self._durations[key])

    @contextmanager
    def timer(self, key: str) -> Generator[None, None, None]:
        """Add the time spent inside the block to the duration under the key."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self._durations[key] += time.perf_counter() - start


_current_stats: ContextVar[ImportStats | None] = ContextVar('import_stats', default=None)


@contextmanager
def collect_import_stats() -> Generator[ImportStats, None, None]:
    """Make a new statistics object current for the duration of the block."""
    stats = ImportStats()
    token = _current_stats.set(stats)
    try:
        yield stats
    finally:
        _current_stats.reset(token)


def current_stats() -> ImportStats:
    """
    Return the statistics of the current import.

    Outside of collect_import_stats() a detached object is returned,
    so the collected values are discarded.
    """
    return _current_stats.get() or ImportStats()


def counted(key: str):
    """Count calls of the decorated function in the current import statistics."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            current_stats().incr(key)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def timed(key: str):
    """Add the time spent in the decorated function to the current import statistics."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with current_stats().timer(key):
                return func(*args, **kwargs)

        return wrapper

    return decorator
