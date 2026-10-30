# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

from django.test import TestCase, override_settings
from rest_framework.exceptions import PermissionDenied, ValidationError

from bublik.core.config.management import ConfigManagementService
from bublik.data.models import Config, ConfigTypes, Project, User, UserRoles


_LOCMEM = {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}
_DUMMY = {'BACKEND': 'django.core.cache.backends.dummy.DummyCache'}

CONTENT_V1 = {'note': 'v1'}
CONTENT_V2 = {'note': 'v2'}


@override_settings(CACHES={'default': _DUMMY, 'run': _LOCMEM, 'project': _LOCMEM})
class ConfigManagementServiceTest(TestCase):
    def setUp(self):
        self.project = Project.objects.create(name='project')
        self.admin = User.objects.create_user(
            email='admin@example.com',
            password='pw12345!',
            roles=UserRoles.ADMIN,
        )
        self.user = User.objects.create_user(email='user@example.com', password='pw12345!')

    def _create(self, content=CONTENT_V1, **overrides):
        data = {
            'type': ConfigTypes.SCHEDULE,
            'name': 'nightly',
            'project': self.project.id,
            'is_active': True,
            'description': 'first',
            'content': content,
        }
        data.update(overrides)
        return ConfigManagementService.create(data, self.admin)

    def test_create_records_the_user_and_activates(self):
        config = self._create()

        assert config.user == self.admin
        assert config.is_active
        assert config.version == 0

    def test_create_rejects_a_name_that_already_exists(self):
        self._create()

        with self.assertRaises(ValidationError):
            self._create(content=CONTENT_V2)

    def test_update_without_content_edits_in_place(self):
        config = self._create()

        updated, created = ConfigManagementService.update(
            config,
            {'description': 'renamed description'},
            self.admin,
        )

        assert not created
        assert updated.id == config.id
        assert updated.description == 'renamed description'
        assert Config.objects.count() == 1

    def test_update_with_new_content_creates_a_new_active_version(self):
        config = self._create()

        updated, created = ConfigManagementService.update(
            config,
            {'content': CONTENT_V2},
            self.admin,
        )
        config.refresh_from_db()

        assert created
        assert updated.id != config.id
        assert updated.version == 1
        assert updated.is_active
        assert updated.user == self.admin
        assert not config.is_active

    def test_update_with_existing_content_repoints_the_active_flag(self):
        first = self._create()
        second, _ = ConfigManagementService.update(first, {'content': CONTENT_V2}, self.admin)

        result, created = ConfigManagementService.update(
            second,
            {'content': CONTENT_V1, 'is_active': True, 'description': 'back to v1'},
            self.admin,
        )
        first.refresh_from_db()
        second.refresh_from_db()

        assert not created
        assert result.id == first.id
        assert first.is_active
        assert first.description == 'back to v1'
        assert not second.is_active
        assert Config.objects.count() == 2  # noqa: PLR2004

    def test_update_with_a_name_renames_every_version(self):
        first = self._create()
        second, _ = ConfigManagementService.update(first, {'content': CONTENT_V2}, self.admin)

        ConfigManagementService.update(second, {'name': 'weekly'}, self.admin)

        names = set(Config.objects.values_list('name', flat=True))
        assert names == {'weekly'}

    def test_update_keeping_the_name_is_not_a_rename(self):
        config = self._create()

        updated, created = ConfigManagementService.update(
            config,
            {'name': 'nightly', 'content': CONTENT_V2},
            self.admin,
        )

        assert created
        assert updated.name == 'nightly'

    def test_a_failed_update_does_not_rename(self):
        config = self._create()

        with self.assertRaises(ValidationError):
            ConfigManagementService.update(
                config,
                {'name': 'weekly', 'content': 'not json'},
                self.admin,
            )

        config.refresh_from_db()
        assert config.name == 'nightly'

    def test_writes_require_an_admin(self):
        config = self._create()

        with self.assertRaises(PermissionDenied):
            ConfigManagementService.create({}, self.user)
        with self.assertRaises(PermissionDenied):
            ConfigManagementService.update(config, {'description': 'x'}, self.user)
        with self.assertRaises(PermissionDenied):
            ConfigManagementService.delete(config, None)

    def test_update_deactivates_when_asked(self):
        config = self._create()

        updated, _ = ConfigManagementService.update(config, {'is_active': False}, self.admin)

        assert not updated.is_active

    def test_delete_removes_the_version(self):
        config = self._create()

        ConfigManagementService.delete(config, self.admin)

        assert not Config.objects.filter(pk=config.pk).exists()

    def test_visible_for_admin_includes_everything(self):
        config = self._create()

        visible = ConfigManagementService.visible_for(self.admin)

        assert config in visible

    def test_visible_for_plain_user_hides_project_configs_by_default(self):
        self._create()

        visible = ConfigManagementService.visible_for(self.user)

        assert not visible.exists()

    def test_visible_for_keeps_the_callers_filters(self):
        self._create()
        Config.objects.create(
            type=ConfigTypes.REPORT,
            name='default-report',
            project=None,
            is_active=True,
            description='',
            user=self.admin,
            content={},
        )

        visible = ConfigManagementService.visible_for(
            self.admin,
            Config.objects.filter(type=ConfigTypes.SCHEDULE),
        )

        assert {config.type for config in visible} == {ConfigTypes.SCHEDULE}

    def test_summarize_reports_one_row_per_config(self):
        first = self._create()
        ConfigManagementService.update(first, {'content': CONTENT_V2}, self.admin)

        rows = ConfigManagementService.summarize(Config.objects.all())

        assert len(rows) == 1
        assert rows[0]['version'] == 1
        assert rows[0]['is_active']

    def test_available_types_names_lists_global_names(self):
        entries = ConfigManagementService.available_types_names()

        types = {entry['type'] for entry in entries}
        assert types == {ConfigTypes.REPORT, ConfigTypes.SCHEDULE, ConfigTypes.GLOBAL}
        assert any(entry.get('name') == 'per_conf' and entry['required'] for entry in entries)
