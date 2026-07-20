# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Helpers for testing import-time feature gates in the root URLconf.

``bublik.urls`` registers the chat (and analytics) routes only when the
corresponding setting is enabled, and that check runs once, at import time.
``override_settings`` therefore cannot turn those routes on or off on its own --
the URLconf module has to be re-imported while the override is active.
"""

from __future__ import annotations

import contextlib
import importlib

from django.conf import settings
from django.urls import clear_url_caches


@contextlib.contextmanager
def reload_urlconf():
    """Re-import the root URLconf, restoring the original one on exit.

    Both the module and Django's resolver cache are refreshed, so
    ``reverse()`` inside the block reflects the settings currently in force.
    """
    urlconf = importlib.import_module(settings.ROOT_URLCONF)
    importlib.reload(urlconf)
    clear_url_caches()
    try:
        yield
    finally:
        importlib.reload(urlconf)
        clear_url_caches()


class ReloadUrlconfMixin:
    """Mixin that reloads the root URLconf for the lifetime of a test class.

    Combine with ``@override_settings(...)`` on the class: Django applies the
    override in ``SimpleTestCase.setUpClass``, so by the time this mixin
    reloads the URLconf the overridden flags are already in force.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._urlconf_ctx = reload_urlconf()
        cls._urlconf_ctx.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._urlconf_ctx.__exit__(None, None, None)
        super().tearDownClass()
