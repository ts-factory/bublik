# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Creating, versioning and deleting configs on behalf of an admin.

Separate from :mod:`bublik.core.config.services`, which the config serializer
imports, to avoid a circular import.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from rest_framework.exceptions import PermissionDenied

from bublik.core.auth import NOT_AUTHORIZED, is_admin
from bublik.core.config.services import ConfigServices
from bublik.core.exceptions import NotFoundError
from bublik.core.shortcuts import serialize
from bublik.data.models import Config, ConfigTypes, GlobalConfigs
from bublik.data.serializers import ConfigSerializer


# The fields a caller may change on an existing config. ``name`` is handled
# separately because a rename applies to every version at once.
UPDATABLE_FIELDS = ('description', 'is_active', 'content')


def _require_admin(user):
    if not is_admin(user):
        raise PermissionDenied(NOT_AUTHORIZED)


def _reads_configs(project_id):
    return 'read_configs' in ConfigServices.getattr_from_global(
        GlobalConfigs.PER_CONF.name,
        'NOT_PERMISSION_REQUIRED_ACTIONS',
        project_id=project_id,
    )


class ConfigManagementService:
    @staticmethod
    def get(config_id: int | str) -> Config:
        """
        Get a config by ID.

        Raises:
            NotFoundError: if the config does not exist
        """
        try:
            return Config.objects.get(pk=config_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Config {config_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def visible_for(user, configs=None):
        """
        Restrict ``configs`` to the ones ``user`` may read.

        Admins see all of them, as does everyone when the default per_conf
        lists ``read_configs`` in NOT_PERMISSION_REQUIRED_ACTIONS. Otherwise
        only the configs of projects whose per_conf lists it are visible, plus
        the default configs when any of those exist.
        """
        if configs is None:
            configs = Config.objects.all()

        if is_admin(user) or _reads_configs(None):
            return configs

        project_ids = [
            project_id
            for project_id in configs.exclude(project__isnull=True)
            .values_list('project_id', flat=True)
            .distinct()
            if _reads_configs(project_id)
        ]
        visible = configs.filter(project_id__in=project_ids)
        if visible.exists():
            return visible | configs.filter(project__isnull=True)
        return configs.none()

    @staticmethod
    def summarize(configs) -> list[dict]:
        """One row per (project, type, name): the active version, else the newest."""
        return list(
            configs.order_by('project', 'type', 'name', '-is_active', '-created')
            .distinct('project', 'type', 'name')
            .values(
                'id',
                'version',
                'is_active',
                'type',
                'name',
                'description',
                'project',
                'created',
            ),
        )

    @staticmethod
    def available_types_names() -> list[dict]:
        """The config types (and, for global configs, names) that may be created."""
        config_type_names = [
            {
                'type': ConfigTypes.REPORT,
                'required': False,
                'description': 'Configuration for report generation',
            },
            {
                'type': ConfigTypes.SCHEDULE,
                'required': False,
                'description': 'Schedule of the runs to be made',
            },
        ]
        for global_config in GlobalConfigs:
            config_type_names.append(
                {
                    'type': ConfigTypes.GLOBAL,
                    'name': global_config.name,
                    'required': global_config in GlobalConfigs.required(),
                    'description': global_config.description,
                },
            )
        return config_type_names

    @staticmethod
    def create(data: dict, user) -> Config:
        """
        Create a config attributed to ``user``, who must be an admin.

        Args:
            data: type, name, project, is_active, description and content

        Raises:
            PermissionDenied: if ``user`` is not an admin
            ValidationError: if the data fails the serializer's validation
        """
        _require_admin(user)
        config, _ = serialize(ConfigSerializer, data, context={'user': user}).get_or_create()
        return config

    @staticmethod
    def update(config: Config, data: dict, user) -> tuple[Config, bool]:
        """
        Apply a partial update to a config, exactly as PATCH does.

        A new ``name`` renames every version of the config. Without ``content``
        the given config is updated in place (``is_active=True`` activates
        it). New ``content`` creates a new version; content equal to an
        existing version re-points that version's ``is_active`` and
        ``description`` instead. Nothing is saved if any part fails.

        Args:
            data: any of name, description, is_active and content
            user: The admin a new version is attributed to

        Returns:
            The resulting config and whether a new version was created

        Raises:
            PermissionDenied: if ``user`` is not an admin
            ValidationError: if the data fails the serializer's validation
        """
        _require_admin(user)
        with transaction.atomic():
            if 'name' in data and data['name'] != config.name:
                serializer = serialize(
                    ConfigSerializer,
                    {'name': data['name']},
                    instance=config,
                    partial=True,
                )
                Config.objects.get_all_versions(
                    config.type,
                    config.name,
                    config.project,
                ).update(
                    name=serializer.validated_data['name'],
                )
                config.refresh_from_db()

            update_data = {k: v for k, v in data.items() if k in UPDATABLE_FIELDS}

            if not update_data:
                return config, False

            if 'content' not in update_data:
                return serialize(
                    ConfigSerializer,
                    update_data,
                    instance=config,
                    partial=True,
                ).save(), False

            updated_config, created = serialize(
                ConfigSerializer,
                update_data,
                instance=config,
                partial=True,
                context={'user': user},
            ).get_or_create()
            if created:
                return updated_config, True

            update_data.setdefault('is_active', config.is_active)
            update_data.setdefault('description', config.description)
            return serialize(
                ConfigSerializer,
                update_data,
                instance=updated_config,
                partial=True,
            ).save(), False

    @staticmethod
    def delete(config: Config, user) -> None:
        """
        Delete one config version.

        Raises:
            PermissionDenied: if ``user`` is not an admin
        """
        _require_admin(user)
        config.delete()
