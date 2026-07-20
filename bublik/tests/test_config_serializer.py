# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from unittest import mock

from django.test import TestCase, override_settings
from rest_framework import serializers

from bublik.data.models import Config, ConfigTypes, GlobalConfigs, Project, User
from bublik.data.serializers import ConfigSerializer
from bublik.interfaces.management.commands.assign_project_by_meta import Command


_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class ConfigSerializerScopeTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='project')
        self.user = User.get_or_create_system_user()

    def test_rejects_project_ai_create(self):
        serializer = ConfigSerializer(
            data={
                'type': ConfigTypes.GLOBAL,
                'name': GlobalConfigs.AI.name,
                'project': self.project.id,
                'is_active': False,
                'content': {'providers': []},
            },
        )

        assert not serializer.is_valid()
        assert 'project' in serializer.errors

    def test_rejects_project_global_rename_to_ai(self):
        config = Config.objects.create(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigs.PER_CONF.name,
            project=self.project,
            is_active=False,
            description='',
            user=self.user,
            content={},
        )
        serializer = ConfigSerializer(
            config,
            data={'name': GlobalConfigs.AI.name},
            partial=True,
        )

        assert not serializer.is_valid()
        assert 'project' in serializer.errors

    def test_initialize_rejects_project_ai(self):
        with self.assertRaisesMessage(
            serializers.ValidationError,
            'AI configuration is only supported for No Project (Default).',
        ):
            ConfigSerializer.initialize(
                {
                    'type': ConfigTypes.GLOBAL,
                    'name': GlobalConfigs.AI.name,
                    'project': self.project,
                    'description': '',
                    'content': {'providers': []},
                },
            )

        assert not Config.objects.filter(name=GlobalConfigs.AI.name).exists()

    def test_initialize_allows_default_ai(self):
        config = ConfigSerializer.initialize(
            {
                'type': ConfigTypes.GLOBAL,
                'name': GlobalConfigs.AI.name,
                'project': None,
                'description': '',
                'content': {'providers': []},
            },
        )

        assert config.project is None

    def test_initialize_allows_other_project_global(self):
        config = ConfigSerializer.initialize(
            {
                'type': ConfigTypes.GLOBAL,
                'name': GlobalConfigs.PER_CONF.name,
                'project': self.project,
                'description': '',
                'content': {},
            },
        )

        assert config.project == self.project


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class AssignProjectConfigScopeTest(TestCase):
    def test_does_not_copy_ai_config(self):
        source = Project.objects.create(name='source')
        target = Project.objects.create(name='target')
        user = User.get_or_create_system_user()
        Config.objects.create(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigs.AI.name,
            project=source,
            is_active=True,
            description='',
            user=user,
            content={'providers': []},
        )
        Config.objects.create(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigs.PER_CONF.name,
            project=source,
            is_active=True,
            description='',
            user=user,
            content={'UI_VERSION': 2},
        )
        runs = mock.Mock()
        runs.values_list.return_value.distinct.return_value = [source.id]

        with mock.patch.object(ConfigSerializer, 'initialize') as initialize:
            Command().init_project_configs(target, runs)

        initialize.assert_called_once()
        assert initialize.call_args.args[0]['name'] == GlobalConfigs.PER_CONF.name

    @mock.patch('bublik.interfaces.management.commands.assign_project_by_meta.call_command')
    @mock.patch(
        'bublik.interfaces.management.commands.assign_project_by_meta.Meta.objects.filter'
    )
    def test_initial_cleanup_preserves_default_ai_config(self, meta_filter, call_command):
        user = User.get_or_create_system_user()
        ai_config = Config.objects.create(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigs.AI.name,
            project=None,
            is_active=True,
            description='',
            user=user,
            content={'providers': []},
        )
        legacy_config = Config.objects.create(
            type=ConfigTypes.GLOBAL,
            name=GlobalConfigs.PER_CONF.name,
            project=None,
            is_active=True,
            description='',
            user=user,
            content={'UI_VERSION': 2},
        )
        runs = mock.Mock()
        runs.exists.return_value = True
        runs.count.return_value = 1
        metas = mock.MagicMock()
        metas.distinct.return_value = metas
        metas.values_list.return_value = []
        metas.__iter__.return_value = iter(())
        command = Command()
        command.is_meta_valid = mock.Mock(return_value=(True, 'Valid meta.'))
        command.get_runs_to_migrate = mock.Mock(return_value=runs)
        meta_filter.return_value = metas

        command.handle(meta='PROJECT')

        assert Config.objects.filter(pk=ai_config.pk).exists()
        assert not Config.objects.filter(pk=legacy_config.pk).exists()
        call_command.assert_called_once_with('meta_categorization')
