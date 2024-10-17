# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from datetime import datetime
import json

import jsonschema

from jsonschema import validate
from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.core.auth import get_user_by_access_token
from bublik.core.queries import get_or_none
from bublik.core.run.utils import prepare_date
from bublik.data.models import Config, ConfigTypes, GlobalConfigNames, User
from bublik.data.schemas.services import load_schema


__all__ = [
    'ConfigSerializer',
]


class ConfigSerializer(ModelSerializer):
    class Meta:
        model = Config
        fields = (
            'id',
            'created',
            'type',
            'name',
            'version',
            'is_active',
            'description',
            'user',
            'content',
        )

    def new_version(self):
        config = Config.get_latest_version(self.initial_data['type'], self.initial_data['name'])
        if config:
            return config.version + 1
        return 0

    def update_data(self):
        '''
        Update initial data with created time, version and user ID.
        '''
        request = self.context['request']
        access_token = request.COOKIES.get('access_token')

        self.initial_data['created'] = prepare_date(datetime.now())
        self.initial_data['version'] = self.new_version()
        self.initial_data['user'] = get_user_by_access_token(access_token).id

    def get_data(self):
        '''
        A universal method for obtaining data that takes into account both the object
        with which the serializer can be initialized and the data.
        '''
        if hasattr(self, 'initial_data') and self.instance:
            data = self.to_representation(self.instance)
            data.update(self.initial_data)
            return data
        if hasattr(self, 'initial_data'):
            return self.initial_data
        if self.instance:
            return self.to_representation(self.instance)
        return {}

    def ensure_json(self, config_content):
        if isinstance(config_content, (dict, list)):
            return config_content
        try:
            return json.loads(config_content)
        except (json.JSONDecodeError, TypeError) as e:
            msg = 'Invalid format: JSON is expected'
            raise serializers.ValidationError(msg) from e

    @classmethod
    def get_json_schema(cls, config_type, config_name):
        if config_type == ConfigTypes.REPORT:
            return load_schema('report')
        if config_type == ConfigTypes.GLOBAL and config_name == GlobalConfigNames.PER_CONF:
            return load_schema('per_conf')
        return None

    def validate_type(self, config_type):
        possible_config_types = ConfigTypes.all()
        if config_type not in possible_config_types:
            msg = f'Unsupported config type. Possible are: {possible_config_types}'
            raise serializers.ValidationError(msg)
        return config_type

    def validate_name(self, name):
        possible_global_config_names = GlobalConfigNames.all()
        if (
            self.initial_data['type'] == ConfigTypes.GLOBAL
            and name not in possible_global_config_names
        ):
            msg = (
                f'Unsupported global config name. Possible are: {possible_global_config_names}'
            )
            raise serializers.ValidationError(msg)
        return name

    def validate_content(self, content):
        '''
        Do the preprocessing and validate config content using the appropriate JSON schema.
        '''
        content = self.ensure_json(content)
        data = self.get_data()
        json_schema = self.__class__.get_json_schema(data['type'], data['name'])
        if json_schema:
            try:
                validate(instance=content, schema=json_schema)
            except jsonschema.exceptions.ValidationError as jeve:
                jeve_msg = jeve.message[0].lower() + jeve.message[1:]
                msg = f'Invalid format: {jeve_msg}'
                raise serializers.ValidationError(msg) from jeve
        return content

    def get_or_create(self, config_data):
        config = get_or_none(
            Config.objects,
            content=config_data['content'],
        )
        if config:
            return config, False
        config = self.create(config_data)
        return config, True

    def create(self, config_data):
        if not isinstance(config_data['user'], User):
            config_data['user'] = User.objects.get(id=config_data['user'])
        is_active = config_data['is_active']
        if is_active:
            config_type = config_data['type']
            config_name = config_data['name']
            active = Config.get_active_version(config_type, config_name)
            if active:
                active.is_active = False
                active.save()
        return Config.objects.create(**config_data)
