# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db import transaction
from rest_framework.serializers import ModelSerializer

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.meta.categorization import categorize_meta
from bublik.core.shortcuts import serialize
from bublik.data.models import Expectation, ExpectMeta
from bublik.data.serializers.meta import MetaSerializer
from bublik.data.serializers.reference import ReferenceSerializer


__all__ = [
    'ExpectMetaReadSerializer',
    'ExpectMetaWriteSerializer',
    'ExpectationSerializer',
]


class ExpectMetaReadSerializer(ModelSerializer):
    '''
    Allows data validation and forbids instances creation.
    Used for an expectation hash calculation.
    Default values are set only in update() and create().
    '''

    meta = MetaSerializer(required=True)
    reference = ReferenceSerializer(required=False, default=None, allow_null=True)

    class Meta:
        model = ExpectMeta
        fields = model.hashable
        extra_kwargs = {'serial': {'default': 0}}

    def update(self):
        msg = 'This method is unavailable in read-only serializer.'
        raise AttributeError(msg)

    def create(self):
        msg = 'This method is unavailable in read-only serializer.'
        raise AttributeError(msg)


class ExpectMetaWriteSerializer(ModelSerializer):
    '''Used to create ExpectMeta objects with related meta and reference.'''

    meta = MetaSerializer(required=True)
    reference = ReferenceSerializer(required=False, default=None, allow_null=True)

    class Meta:
        model = ExpectMeta
        fields = ('id', 'meta', 'reference', 'expectation', 'serial')
        extra_kwargs = {'serial': {'default': 0}}

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

        return ExpectMeta.objects.get_or_create(
            **self.validated_data,
            meta=meta,
            reference=reference,
        )


class ExpectationSerializer(HashedModelSerializer):
    '''
    Allows re-using an expectation object that points to the same metas.
    For this an expectation is hashed based on the related expect metas.

    ExpectMetaReadSerializer is used for a hash calculation and
    ExpectMetaWriteSerializer - to create expect metas.
    This is necessary because of backward relationships, as expect metas
    store a reference to some expectation whereas an expectation hash
    is based on a set of such expect metas.

    The transaction of get_or_create() is atomic to prevent an Expectation
    instance to be created without expect metas if errors occurred
    in its getting or creating process.
    '''

    expectmeta_set = ExpectMetaReadSerializer(many=True, required=True)

    class Meta:
        model = Expectation
        fields = model.hashable

    @transaction.atomic
    def get_or_create(self):
        e_hash = self.validated_data_and_hash.get('hash')
        e = self.get_or_none(hash=e_hash)
        if e:
            return e, False

        expect_metas = self.validated_data_and_hash.pop('expectmeta_set')
        e = Expectation.objects.create(**self.validated_data_and_hash)

        for em_data in expect_metas:
            em_data.update({'expectation': e.pk})
            em_serializer = serialize(ExpectMetaWriteSerializer, em_data)
            em_serializer.get_or_create()

        return e, True
