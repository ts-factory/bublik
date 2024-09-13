# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.


from bublik.core.hash_system import HashedModelSerializer
from bublik.core.utils import empty_to_none
from bublik.data.models import Meta


__all__ = [
    'MetaSerializer',
]


class MetaSerializer(HashedModelSerializer):
    class Meta:
        model = Meta
        fields = model.hashable
        extra_kwargs = {
            'name': {'default': None},
            'comment': {'default': None},
            'value': {'trim_whitespace': False, 'default': None},
        }

    def to_internal_value(self, data):
        empty_to_none(data, ['name', 'comment'])
        return super().to_internal_value(data)
