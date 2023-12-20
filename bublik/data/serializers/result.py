# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework.serializers import ModelSerializer

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.meta.categorization import categorize_meta
from bublik.core.shortcuts import serialize
from bublik.core.utils import empty_to_none
from bublik.data.models import (
    MetaResult,
    Test,
    TestArgument,
    TestIteration,
    TestIterationRelation,
    TestIterationResult,
)
from bublik.data.serializers.meta import MetaSerializer
from bublik.data.serializers.reference import ReferenceSerializer


__all__ = [
    'TestSerializer',
    'TestArgumentSerializer',
    'TestIterationSerializer',
    'TestIterationRelationSerializer',
    'TestIterationResultSerializer',
    'MetaResultSerializer',
]


class TestSerializer(ModelSerializer):
    class Meta:
        model = Test
        fields = ('id', 'name', 'parent', 'result_type')


class TestArgumentSerializer(HashedModelSerializer):
    class Meta:
        model = TestArgument
        fields = model.hashable
        extra_kwargs = {'value': {'trim_whitespace': False}}


class TestIterationSerializer(ModelSerializer):
    class Meta:
        model = TestIteration
        fields = ('id', 'test', 'test_arguments', 'hash')


class TestIterationRelationSerializer(ModelSerializer):
    class Meta:
        model = TestIterationRelation
        fields = ('id', 'test_iteration', 'parent_iteration', 'depth')


class TestIterationResultSerializer(ModelSerializer):
    class Meta:
        model = TestIterationResult
        fields = (
            'id',
            'iteration',
            'test_run',
            'parent_package',
            'tin',
            'exec_seqno',
            'start',
            'finish',
        )


class MetaResultSerializer(ModelSerializer):
    meta = MetaSerializer(required=True)
    reference = ReferenceSerializer(required=False, default=None, allow_null=True)

    class Meta:
        model = MetaResult
        fields = ('id', 'meta', 'reference', 'result', 'ref_index', 'serial')
        extra_kwargs = {
            'ref_index': {'default': None},
            'serial': {'default': 0},
        }

    def to_internal_value(self, data):
        data = empty_to_none(data, ['ref_index'])
        return super().to_internal_value(data)

    def get_or_create(self):
        meta_data = self.validated_data.pop('meta')
        meta_serializer = serialize(MetaSerializer, meta_data)
        meta, created = meta_serializer.get_or_create()
        if created:
            categorize_meta(meta)

        reference = self.validated_data.pop('reference', None)
        if reference:
            reference_serializer = serialize(ReferenceSerializer, reference)
            reference, _ = reference_serializer.get_or_create()

        return MetaResult.objects.get_or_create(
            **self.validated_data,
            meta=meta,
            reference=reference,
        )
