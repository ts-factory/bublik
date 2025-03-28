# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.


from typing import ClassVar

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

    @classmethod
    def get_or_create_project(cls, project_name):
        m_data = {
            'name': 'PROJECT',
            'type': 'label',
            'value': project_name,
        }
        meta_serializer = serialize(cls, m_data)
        project_meta, created = meta_serializer.get_or_create()
        if created:
            categorize_meta(project_meta)
        return project_meta, created
