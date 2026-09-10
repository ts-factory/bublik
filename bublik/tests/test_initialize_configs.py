# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Bootstrapping of the required (and chat-dependent) global configs.

``initialize_configs`` is the only supported way a deployment gets its default
global configs -- ``scripts/deploy`` runs it in the ``per_project_conf`` step.
The ``ai`` config joins them only when chat is enabled, because a missing one
reads back as an empty provider set rather than falling back to the schema
default, leaving chat with nothing to offer.
"""

from io import StringIO
import os
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from bublik.data.models import Config, ConfigTypes, GlobalConfigs
from bublik.data.schemas.services import load_schema


_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}


@mock.patch.dict(os.environ)
@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class InitializeConfigsTest(TestCase):
    def setUp(self):
        # Creating the `meta` config triggers meta_categorization, which logs
        # to a Celery task file when TASK_ID is set. test_importruns_api leaves
        # it in os.environ, so drop it for the lifetime of these tests
        # (patch.dict restores the environment afterwards).
        os.environ.pop('TASK_ID', None)
        Config.objects.all().delete()

    @staticmethod
    def initialize():
        call_command('initialize_configs', stdout=StringIO())

    @staticmethod
    def global_names():
        return set(
            Config.objects.filter(type=ConfigTypes.GLOBAL, project=None).values_list(
                'name',
                flat=True,
            ),
        )

    @override_settings(AI_CHAT_ENABLED=False)
    def test_chat_disabled_initializes_only_the_required_configs(self):
        self.initialize()

        assert self.global_names() == {config.name for config in GlobalConfigs.required()}

    @override_settings(AI_CHAT_ENABLED=True)
    def test_chat_enabled_also_initializes_the_ai_config(self):
        self.initialize()

        expected = {config.name for config in GlobalConfigs.required()}
        expected.add(GlobalConfigs.AI.name)
        assert self.global_names() == expected

    @override_settings(AI_CHAT_ENABLED=True)
    def test_ai_config_is_seeded_from_the_schema_default(self):
        self.initialize()

        config = Config.objects.get_global(GlobalConfigs.AI.name, None)
        assert config.content == load_schema('ai')['default']
        assert config.is_active

    @override_settings(AI_CHAT_ENABLED=True)
    def test_initialization_is_idempotent(self):
        self.initialize()
        config = Config.objects.get_global(GlobalConfigs.AI.name, None)

        self.initialize()

        assert Config.objects.get_global(GlobalConfigs.AI.name, None).id == config.id
