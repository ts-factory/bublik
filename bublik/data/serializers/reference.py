# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework.serializers import ModelSerializer

from bublik.data.models import Reference


__all__ = [
    'ReferenceSerializer',
]


class ReferenceSerializer(ModelSerializer):
    class Meta:
        model = Reference
        fields = model.hashable

    def get_unique_together_validators(self):
        """
        Overriding method to disable unique together checks in order to
        implement 'get_or_create' method.
        """
        return []

    def get_or_create(self):
        return Reference.objects.get_or_create(**self.validated_data)
