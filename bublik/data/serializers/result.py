# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import json
from typing import ClassVar

from rest_framework import serializers
from rest_framework.serializers import ModelSerializer

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.meta.categorization import categorize_meta
from bublik.core.shortcuts import serialize
from bublik.core.utils import empty_to_none
from bublik.data.models import (
    MetaResult,
    MetaTest,
    Test,
    TestArgument,
    TestIteration,
    TestIterationRelation,
    TestIterationResult,
)
from bublik.data.serializers.meta import MetaSerializer
from bublik.data.serializers.reference import ReferenceSerializer


__all__ = [
    'MetaResultSerializer',
    'MetaTestSerializer',
    'TestArgumentSerializer',
    'TestIterationRelationSerializer',
    'TestIterationResultSerializer',
    'TestIterationSerializer',
    'TestSerializer',
]


class TestSerializer(ModelSerializer):
    class Meta:
        model = Test
        fields = ('id', 'name', 'parent', 'result_type')


class TestArgumentSerializer(HashedModelSerializer):
    class Meta:
        model = TestArgument
        fields = model.hashable
        extra_kwargs: ClassVar[dict] = {'value': {'trim_whitespace': False}}


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
        extra_kwargs: ClassVar[dict] = {
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


class MetaTestSerializer(ModelSerializer):
    comment = serializers.CharField(
        source='meta.value',
        help_text=(
            'This field represents the value of the meta object corresponding to the comment'
        ),
    )

    class Meta:
        model = MetaTest
        fields = ('id', 'updated', 'test', 'comment', 'serial')
        extra_kwargs: ClassVar[dict] = {
            'test': {'read_only': True},
            'serial': {'read_only': True},
        }

    def to_internal_value(self, data):
        internal = super().to_internal_value(data)
        internal['serial'] = self.context.get('serial')
        internal['test'] = self.context.get('test')

        meta_data = internal.get('meta', {})
        meta_data.update({'type': 'comment', 'value': json.dumps(meta_data.get('value'))})
        internal['meta'] = meta_data

        return internal

    def validate(self, attrs):
        test = attrs.get('test')
        if test is None:
            msg = '\'test\' must be provided in serializer context'
            raise serializers.ValidationError(msg)

        meta_serializer = serialize(MetaSerializer, attrs['meta'])
        meta, _ = meta_serializer.get_or_create()

        if MetaTest.objects.filter(test=test, meta=meta).exists():
            msg = 'This comment already exists for this test'
            raise serializers.ValidationError(msg)

        attrs['meta'] = meta
        return attrs
