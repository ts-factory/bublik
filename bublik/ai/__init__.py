# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Bublik chat assistant package.

The public surface external callers should depend on:

* :func:`build_chat_routes` -- the AG-UI Starlette routes mounted in
  ``bublik.asgi`` (``bublik.ai.app``).
* :func:`build_agent` -- the cached Pydantic-AI agent factory
  (``bublik.ai.agent``); ``bublik.interfaces.signals`` clears its cache.
* :mod:`run_store` -- the Redis-backed resumable-run store
  (``bublik.ai.run_store``); the chat-thread DRF views read run status from it.

Re-exports are resolved lazily via :pep:`562` ``__getattr__`` so importing the
package (e.g. the DRF views' ``from bublik.ai import run_store``) does not
eagerly pull in pydantic-ai / the ORM before Django's app registry is ready.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from bublik.ai import run_store
    from bublik.ai.agent import build_agent
    from bublik.ai.app import build_chat_routes


__all__ = ['build_agent', 'build_chat_routes', 'run_store']

# Attribute name -> (submodule, symbol). ``None`` symbol means the submodule
# itself is the exported object.
_EXPORTS = {
    'build_chat_routes': ('bublik.ai.app', 'build_chat_routes'),
    'build_agent': ('bublik.ai.agent', 'build_agent'),
    'run_store': ('bublik.ai.run_store', None),
}


def __getattr__(name: str) -> object:
    try:
        module_name, symbol = _EXPORTS[name]
    except KeyError:
        msg = f'module {__name__!r} has no attribute {name!r}'
        raise AttributeError(msg) from None
    module = import_module(module_name)
    return module if symbol is None else getattr(module, symbol)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
