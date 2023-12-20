# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework.serializers import ModelSerializer

from bublik.core.hash_system import HashedModelSerializer
from bublik.core.measurement.representation import MeasurementRepresentation
from bublik.core.meta.categorization import categorize_meta
from bublik.core.shortcuts import serialize
from bublik.data.models import Measurement, MeasurementResult, View
from bublik.data.serializers.meta import MetaSerializer


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


class MeasurementResultSerializer(ModelSerializer):
    measurement = MeasurementSerializer(read_only=True)

    class Meta:
        model = MeasurementResult
        fields = ('id', 'measurement', 'result', 'value')
        extra_kwargs = {'serial': {'default': 0}}

    def to_representation(self, instance):
        metas = instance.measurement.metas.all().values('name', 'type', 'value')
        mmr = MeasurementRepresentation(metas, instance.value)
        representation = mmr.get_dict(mmr.value)
        representation.update({'id': instance.id})
        return representation


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
