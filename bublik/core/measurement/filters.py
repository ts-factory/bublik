# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.conf import settings
from rest_framework import filters

from bublik.core.queries import get_or_none
from bublik.data.models import Measurement, Meta


class MeasurementResultFilter(filters.BaseFilterBackend):
    '''To use this filter, you must define search_fields in the view.
    Only these fields will be filtered. The result field will be filtered
    by default, and you don't need to specify it.
    '''

    search_field_to_meta_type = {
        'tool': 'tool',
        'name': 'measurement_subject',
        'type': 'measurement_subject',
        'aggr': 'measurement_subject',
        'keys': 'measurement_key',
    }

    def get_search_fields(self, view):
        return getattr(view, 'search_fields', None)

    def get_corresponding_metas(self, search_field, value):
        values = value.split(settings.QUERY_DELIMITER)
        metas = []
        pair_len = 2
        for v in values:
            pair = v.split(settings.KEY_VALUE_DELIMITER, 1)

            m_type = self.search_field_to_meta_type.get(search_field)
            m_fields = ('name', 'value', 'type')

            if len(pair) == pair_len:
                m_values = (pair.pop(0), pair.pop(1), m_type)
            else:
                m_values = (search_field, v, m_type)

            m_data = dict(zip(m_fields, m_values))
            meta = get_or_none(Meta.objects, **m_data)
            if not meta:
                msg = f'Meta with {m_data} does not exist'
                raise ValueError(msg)

            metas.append(meta)
        return metas

    def filter_queryset(self, request, qs, view):
        params = request.query_params
        result = params.get('result_id')

        qs = qs.filter(result__id=result)

        search_fields = self.get_search_fields(view)
        if not search_fields:
            msg = (
                "To use the filter on measurements 'search_fields' should be specified in "
                'the corresponding view.'
            )
            raise NameError(
                msg,
            )

        measurements = Measurement.objects.filter()

        for search_field in search_fields:
            if search_field not in self.search_field_to_meta_type:
                msg = f"Incorrect search field: '{search_field}'"
                raise NameError(msg)

            value = params.get(search_field)
            if value:
                metas = self.get_corresponding_metas(search_field, value)
                measurements &= Measurement.objects.filter(metas__in=metas)

        if measurements:
            qs = qs.filter(measurement__in=measurements)
        elif len(params) > 1:
            msg = 'Measurements with these parameters were not found'
            raise ValueError(msg)

        return qs
