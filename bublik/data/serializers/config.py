# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from collections import Counter
import json
from typing import ClassVar

from django.contrib.auth import get_user_model
from django.db import transaction
import jsonschema
from jsonschema import validate
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.core.auth import get_user_by_access_token
from bublik.core.config.services import ConfigServices
from bublik.core.queries import get_or_none
from bublik.data.models import Config, ConfigTypes, GlobalConfigs, Project


__all__ = [
    'ConfigSerializer',
]


class ConfigSerializer(ModelSerializer):
    class Meta:
        model = Config
        fields = (
            'id',
            'type',
            'name',
            'project',
            'version',
            'is_active',
            'description',
            'user',
            'content',
        )
        extra_kwargs: ClassVar[dict] = {
            'version': {'read_only': True},
            'user': {'read_only': True},
        }
        validators = []

    def to_internal_value(self, data):
        is_system_action = self.context.get('is_system_action', False)
        if is_system_action:
            internal = data
            internal['user'] = get_user_model().get_or_create_system_user()
        else:
            internal = super().to_internal_value(data)
            access_token = self.context.get('access_token', None)
            if access_token:
                internal['user'] = get_user_by_access_token(access_token)
        return internal

    def validate_type(self, config_type):
        possible_config_types = ConfigTypes.all()
        if config_type not in possible_config_types:
            msg = f'Unsupported config type. Possible are: {possible_config_types}'
            raise serializers.ValidationError(msg)
        return config_type

    def validate_name(self, name):
        config_type = self.initial_data.get('type', getattr(self.instance, 'type', None))
        config_project = self.initial_data.get(
            'project',
            getattr(self.instance, 'project', None),
        )

        possible_global_config_names = GlobalConfigs.all()
        if config_type == ConfigTypes.GLOBAL and name not in possible_global_config_names:
            msg = (
                f'Unsupported {config_type} configuration name. '
                f'Possible are: {possible_global_config_names}.'
            )
            raise serializers.ValidationError(msg)

        same_name_configs = Config.objects.filter(
            name=name,
            type=config_type,
            project=config_project,
        )
        if same_name_configs:
            project_name = (
                'default'
                if config_project is None
                else getattr(config_project, 'name', None)
                or Project.objects.get(id=config_project).name
            )
            msg = (
                f'{config_type.capitalize()} configuration \'{name}\' '
                f'already exist for {project_name}'
            )
            raise serializers.ValidationError(msg)

        return name

    def validate_content(self, content):
        '''
        Do the preprocessing and validate config content using the appropriate JSON schema.
        '''

        def ensure_json(config_content):
            if isinstance(config_content, (dict, list)):
                return config_content
            try:
                return json.loads(config_content)
            except (json.JSONDecodeError, TypeError) as e:
                msg = 'Invalid format: JSON is expected'
                raise serializers.ValidationError(msg) from e

        content = ensure_json(content)

        config_type = self.initial_data.get('type', getattr(self.instance, 'type', None))
        config_name = self.initial_data.get('name', getattr(self.instance, 'name', None))
        json_schema = ConfigServices.get_schema(config_type, config_name)
        if json_schema:
            try:
                validate(instance=content, schema=json_schema)
            except jsonschema.exceptions.ValidationError as jeve:
                jeve_msg = jeve.message[0].lower() + jeve.message[1:]
                msg = f'Invalid format: {jeve_msg}'
                raise serializers.ValidationError(msg) from jeve

        if config_type == ConfigTypes.GLOBAL and config_name == GlobalConfigs.META.name:
            category_duplicates = [
                category
                for category, count in Counter(
                    item['category'] for item in content if item.get('category') is not None
                ).items()
                if count > 1
            ]
            if category_duplicates:
                msg = (
                    f'Invalid input: duplicate \'category\' values found: {category_duplicates}'
                )
                raise serializers.ValidationError(msg)
        return content

    @classmethod
    def initialize(cls, config_data):
        '''
        Used for initializing configurations.
        Sets is_active=True, adds user to the provided config data and
        calls create().
        '''
        config_data['is_active'] = True
        serializer = cls(data=config_data, context={'is_system_action': True})
        internal = serializer.to_internal_value(serializer.initial_data)
        return serializer.create(internal)

    @transaction.atomic
    def update(self, instance, validated_data):
        is_active = validated_data.pop('is_active', instance.is_active)
        config = super().update(instance, validated_data)
        if is_active and not config.is_active:
            config.activate()
        elif not is_active and config.is_active:
            config.is_active = False
            config.save(update_fields=['is_active'])
        return config

    def get_or_create(self):
        config_data = {
            **self.validated_data,
            'type': self.validated_data.get('type', getattr(self.instance, 'type', None)),
            'name': self.validated_data.get('name', getattr(self.instance, 'name', None)),
            'project': self.validated_data.get(
                'project',
                getattr(self.instance, 'project', None),
            ),
            'description': self.validated_data.get(
                'description',
                getattr(self.instance, 'description', None),
            ),
            'is_active': self.validated_data.get(
                'is_active',
                getattr(self.instance, 'is_active', None),
            ),
        }
        config = get_or_none(
            Config.objects,
            type=config_data['type'],
            name=config_data['name'],
            project=config_data['project'],
            content=config_data['content'],
        )
        if config:
            return config, False
        config = self.create(config_data)
        return config, True

    def create(self, config_data):
        with transaction.atomic():
            config = Config.objects.create(**{**config_data, 'is_active': False})
            if config_data.get('is_active'):
                config.activate()
        return config
