# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.


from typing import ClassVar

from rest_framework import serializers

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.meta.categorization import categorize_meta
from bublik.core.shortcuts import serialize
from bublik.core.utils import empty_to_none
from bublik.data.models import Meta


__all__ = [
    'MetaSerializer',
]


class MetaSerializer(HashedModelSerializer):
    class Meta:
        model = Meta
        fields = model.hashable
        extra_kwargs: ClassVar[dict] = {
            'name': {'default': None},
            'comment': {'default': None},
            'value': {'trim_whitespace': False, 'default': None},
        }

    def to_internal_value(self, data):
        empty_to_none(data, ['name', 'comment'])
        return super().to_internal_value(data)


class ProjectMetaSerializer(MetaSerializer):
    project_name = serializers.CharField(
        source='value',
        help_text=(
            'This is the name field representing the value of the meta object, '
            'corresponding to the project'
        ),
    )

    class Meta:
        model = Meta
        fields: ClassVar[list] = ['id', 'project_name']

    def get_or_create(self):
        m_data = {
            'name': 'PROJECT',
            'type': 'label',
            'value': self.initial_data['project_name'],
        }
        meta_serializer = serialize(self.__class__.__bases__[0], m_data)
        project_meta, created = meta_serializer.get_or_create()
        if created:
            categorize_meta(project_meta)
        return project_meta, created
