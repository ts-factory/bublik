# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from enum import Enum

from django.core.exceptions import ObjectDoesNotExist
from django.db import models

from bublik.data.models.project import Project
from bublik.data.models.user import User


__all__ = [
    'Config',
    'ConfigTypes',
    'GlobalConfigs',
]


class ConfigTypes(models.TextChoices):
    '''
    All available configuration types.
    '''

    GLOBAL = 'global'
    REPORT = 'report'
    SCHEDULE = 'schedule'

    @classmethod
    def all(cls):
        return [value.value for name, value in vars(cls).items() if name.isupper()]


class GlobalConfigs(Enum):
    '''
    All available global configuration names and descriptions.
    '''

    PER_CONF = ('per_conf', 'The main project configuration')
    REFERENCES = ('references', 'Project references')
    META = ('meta', 'Meta categorization configuration')

    def __init__(self, name, description):
        self._name = name
        self._description = description

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return self._description

    def __str__(self):
        return self.name

    @classmethod
    def all(cls):
        return [config.name for config in cls]

    @classmethod
    def required(cls):
        return [cls.PER_CONF, cls.REFERENCES, cls.META]


class ConfigManager(models.Manager):
    def get_latest(self, config_type, config_name, project_id):
        return (
            self.get_queryset()
            .filter(
                type=config_type,
                name=config_name,
                project=project_id,
            )
            .order_by('version')
            .last()
        )

    def get_active_or_none(self, config_type, config_name, project_id=...):
        active_configs = self.get_queryset().filter(
            type=config_type,
            name=config_name,
            is_active=True,
        )
        try:
            if project_id is ...:
                return active_configs.get()
            return active_configs.get(
                project=project_id,
            )
        except self.model.DoesNotExist:
            return None

    def get_all_versions(self, config_type, config_name, project_id):
        return (
            self.get_queryset()
            .filter(
                type=config_type,
                name=config_name,
                project=project_id,
            )
            .order_by('-is_active', '-created')
            .values('id', 'version', 'is_active', 'description', 'created')
        )

    def get_global(self, config_name, project_id=...):
        config = self.get_active_or_none(ConfigTypes.GLOBAL, config_name, project_id)
        if not config:
            if project_id is ...:
                msg = (
                    f'there is no active {config_name} global configuration object. '
                    'Create one or activate one of the existing ones'
                )
            elif project_id is None:
                msg = (
                    f'there is no active default {config_name} global configuration object. '
                    'Create one or activate one of the existing ones'
                )
            else:
                project_name = Project.objects.get(
                    id=project_id,
                ).name
                msg = (
                    f'there is no active {config_name} global configuration object '
                    f'for {project_name} project. Create one or activate one '
                    f'of the existing ones'
                )
            raise ObjectDoesNotExist(msg)
        return config


class Config(models.Model):
    '''
    Configurations.
    '''

    created = models.DateTimeField(help_text='Timestamp of the config creation.')
    type = models.CharField(
        choices=ConfigTypes.choices,
        max_length=16,
        help_text='Configuration type.',
    )
    name = models.TextField(max_length=32, help_text='Configuration name.')
    version = models.IntegerField(default=0, help_text='Configuration version.')
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        null=True,
        help_text='The project identifier.',
    )
    is_active = models.BooleanField()
    description = models.TextField(
        blank=True,
        help_text='Description of the configuration.',
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='config',
        help_text='The user who created the configuration object.',
    )
    content = models.JSONField(help_text='Configuration data.')

    objects = ConfigManager()

    class Meta:
        db_table = 'bublik_config'
        unique_together = ('type', 'name', 'project', 'version')

    def activate(self):
        active = Config.objects.get_active_or_none(self.type, self.name, self.project)
        if active:
            active.is_active = False
            active.save()
        self.is_active = True
        self.save()

    def delete(self, *args, **kwargs):
        config_type = self.type
        config_name = self.name
        config_project = self.project
        config_active = self.is_active
        super().delete(*args, **kwargs)
        if config_active:
            latest = Config.objects.get_latest(config_type, config_name, config_project)
            if latest:
                latest.activate()

    def __repr__(self):
        return (
            f'Config(created={self.created!r}, project={self.project!r}, type={self.type!r}, '
            f'name={self.name!r}, version={self.version!r}, is_active={self.is_active!r}, '
            f'description={self.description!r}, user={self.user!r})'
        )
