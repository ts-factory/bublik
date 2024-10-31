# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import logging

from rest_framework.serializers import ModelSerializer

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.measurement.representation import MeasurementRepresentation
from bublik.core.meta.categorization import categorize_meta
from bublik.core.shortcuts import serialize
from bublik.data.models import Measurement, MeasurementResult, MeasurementResultList, View
from bublik.data.serializers.meta import MetaSerializer


logger = logging.getLogger('bublik.server')

__all__ = [
    'MeasurementSerializer',
    'MeasurementResultSerializer',
    'ViewSerializer',
]


class MeasurementSerializer(HashedModelSerializer):
    metas = MetaSerializer(required=True, many=True)

    class Meta:
        model = Measurement
        fields = model.hashable

    def get_or_create(self):
        measurement_hash = self.validated_data_and_hash.get('hash')
        measurement = self.get_or_none(hash=measurement_hash)
        if measurement:
            return measurement, False

        metas = self.validated_data_and_hash.pop('metas')
        measurement = Measurement.objects.create(**self.validated_data_and_hash)

        for meta in metas:
            meta_serializer = serialize(MetaSerializer, meta)
            meta, created = meta_serializer.get_or_create()
            if created:
                categorize_meta(meta)
            measurement.metas.add(meta)

        return measurement, True


class MeasurementResultCommonSerializer(ModelSerializer):
    def to_representation(self, instance):
        metas = instance.measurement.metas.all().values('name', 'type', 'value')
        mmr = MeasurementRepresentation(metas, instance.value)
        representation = mmr.get_dict(mmr.value)
        representation.update({'id': instance.id})
        return representation

    def get_or_create(self):
        model = self.Meta.model
        value = self.validated_data.pop('value')
        mmr, created = model.objects.get_or_create(
            **self.validated_data,
            defaults={'value': value},
        )
        if created:
            return mmr, created
        if mmr.value != value:
            logger.warning(
                'a new value was obtained from imported logs for the exist '
                f'{model.__name__} object (id = {mmr.id}). '
                f'Old value is {mmr.value}. New value is {self.value}.',
            )
            mmr.value = self.value
            mmr.save()
        return mmr, created


class MeasurementResultSerializer(MeasurementResultCommonSerializer):
    class Meta:
        model = MeasurementResult
        fields = ('id', 'measurement', 'result', 'serial', 'value')


class MeasurementResultListSerializer(MeasurementResultCommonSerializer):
    class Meta:
        model = MeasurementResultList
        fields = ('id', 'measurement', 'result', 'serial', 'value')


class ViewSerializer(HashedModelSerializer):
    metas = MetaSerializer(required=True, many=True)

    class Meta:
        model = View
        fields = model.hashable

    def get_or_create(self):
        view_hash = self.validated_data_and_hash.get('hash')
        view = self.get_or_none(hash=view_hash)
        if view:
            return view, False

        metas = self.validated_data_and_hash.pop('metas')
        view = View.objects.create(**self.validated_data_and_hash)

        for meta in metas:
            meta_serializer = serialize(MetaSerializer, meta)
            meta, _ = meta_serializer.get_or_create()
            view.metas.add(meta)

        return view, True
