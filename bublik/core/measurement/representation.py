# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db.models import F, QuerySet

from bublik.core.measurement.services import get_measurement_results
from bublik.core.utils import get_metric_prefix_units, key_value_transforming
from bublik.data.models import ChartView


class ChartViewBuilder:
    '''
    Considering the purpose of ChartView model to describe views of two measurements
    on a single graph ChartViewBuilder provides the interface to do this.
    '''

    points = None
    axises_config = None
    measurement_data = None

    def __init__(self, view, measurement, measurement_results):
        view_data = view.representation()
        measurement_data = measurement.representation()
        self.points = self.get_measurement_values(measurement_results)
        self.axises_config = self.prepare_axises_config(view_data, measurement_data)
        self.measurement_data = measurement_data

    def representation(self):
        data = self.measurement_data
        data.update(
            {
                'dots': self.points,
                'axises_config': self.axises_config,
            },
        )
        return data

    @classmethod
    def by_line_graph(cls, cv: ChartView):
        view = cv.view
        measurement = cv.measurement
        measurement_results = get_measurement_results([cv.result.id], measurement)
        return cls(view, measurement, measurement_results)

    @classmethod
    def by_points(cls, cvs: QuerySet[ChartView]):
        # 1. Check dots have the same measurements
        # 2. measurement = cvs[0].measurement
        # 3. iteration_results = cvs.values_list('result', flat=True)
        # 4. measurement_results = get_measurement_results(iteration_results, measurement)
        # 5. view = create manually
        # 6. return cls(view, measurement, measurement_results)
        pass

    def get_measurement_values(self, measurement_results):
        return list(
            measurement_results.annotate(
                start=F('result__start'),
                sequence_number=F('serial'),
                # value=ExpressionWrapper(F('value') * Value(multiplier),
                # output_field=CharField()),
                run_id=F('result__test_run'),
                iteration_id=F('result__iteration_id'),
            )
            .order_by('serial')
            .values(
                'start',
                'sequence_number',
                'value',
                'run_id',
                'result_id',
                'iteration_id',
            ),
        )

    def prepare_axises_config(self, view: dict, measurement: dict):
        title_items = []
        if view['title']:
            title_items.append(view['title'])
        if measurement['aggr']:
            title_items.append(measurement['aggr'].capitalize())
        if measurement['keys']:
            title_items.append(', '.join(measurement['keys']))

        return {
            'title': ' - '.join(title_items),
            'default_x': 'sequence_number',
            'default_y': 'value',
            'getters': ['start', 'value', 'sequence_number'],
            'axises': {
                'start': {
                    'getter': 'start',
                    'label': 'Start of measurement test',
                    'units': 'timestamp',
                },
                'sequence_number': {
                    'getter': 'sequence_number',
                    'label': 'Sequence Number',
                    'units': '',
                },
                'value': {
                    'getter': 'value',
                    'label': measurement['name'],
                    'units': measurement['units'],
                },
            },
        }

    @classmethod
    def get_measurement_chart_label(cls, measurement_data, sequense_group_argument=None):
        label_items = {
            'type': measurement_data['type'],
            'units': measurement_data['units'],
            'aggr': measurement_data['aggr'],
            'sequense_group_arg': sequense_group_argument,
            'tool': measurement_data['tool'],
            'keys': measurement_data['keys'],
        }

        units_aggr_data = ', '.join(
            label_items[key] for key in ['units', 'aggr'] if label_items[key]
        )

        label_parts = {
            'type': (measurement_data['type'][0].upper() + measurement_data['type'][1:])
            if measurement_data['type']
            else None,
            'units_arrg': f'({units_aggr_data})',
            'sga': f'by {label_items["sequense_group_arg"]}'
            if label_items['sequense_group_arg']
            else None,
            'tool': f': based on {label_items["tool"]}' if label_items['tool'] else None,
            'keys': f' ({(", ".join(label_items["keys"]))})' if label_items['keys'] else None,
        }

        return ' '.join(
            [label_part for label_part in label_parts.values() if label_part],
        ).replace(' :', ':')

    @classmethod
    def get_measurement_axis_label(cls, measurement_data):
        measurement_axis_label = (
            measurement_data['name'] if measurement_data['name'] else measurement_data['type']
        )
        if measurement_data['units']:
            measurement_axis_label += f' ({measurement_data["units"]})'
        return measurement_axis_label


class MeasurementRepresentation:
    def __init__(self, metas, value):
        self.comments = []
        self.keys = []
        self.name = None
        self.tool = None
        self.type_measurement = None

        aggr = None
        base_units = None
        multiplier = None

        for m in metas:
            if m['name'] == 'multiplier':
                multiplier = m['value']
            if m['name'] == 'aggr':
                aggr = m['value']
            if m['name'] == 'base_units':
                base_units = m['value']
            if m['name'] == 'type' and m['type'] == 'measurement_subject':
                self.type_measurement = m['value']
            if m['name'] == 'name' and m['type'] == 'measurement_subject':
                self.name = m['value']
            if m['name'] == 'tool':
                self.tool = m['value']
            if m['type'] == 'measurement_key':
                self.keys.append(key_value_transforming(m['name'], m['value']))
            if m['type'] == 'measurement_comment':
                self.comments.append(key_value_transforming(m['name'], m['value']))

        self.value = {
            'value': value,
            'units': get_metric_prefix_units(multiplier, base_units),
            'aggr': aggr,
        }

    def get_dict(self, value):
        return {
            'tool': self.tool,
            'type': self.type_measurement,
            'name': self.name,
            'key': self.keys,
            'comment': self.comments,
            'results': value,
        }

    def get_tuple(self):
        return (self.type_measurement, self.name, self.tool, self.keys, self.comments)

    def __hash__(self):
        return hash(
            (
                self.type_measurement,
                self.tool,
                self.name,
                frozenset(self.keys),
                frozenset(self.comments),
            ),
        )

    def __eq__(self, other):
        return self.get_tuple() == other.get_tuple()


class AxisRepresentationBuilder:
    def __init__(self, measurement, mrs):
        self.measurement = measurement
        self.values = self._get_mr_values_by_measurement(mrs)

        self.label = None
        self.units = None
        self.representation_data = []

    def set_label(self, label=None):
        if self.measurement is None:
            self.label = 'Sequence number'

        elif label is None:
            measure_name = (
                self.measurement.metas.filter(name='name', type='measurement_subject')
                .values_list('value', flat=True)
                .first()
            )
            measure_type = (
                self.measurement.metas.filter(name='type', type='measurement_subject')
                .values_list('value', flat=True)
                .first()
            )

            if measure_name is None:
                self.label = measure_type
            else:
                self.label = measure_name + ' ' + measure_type

        else:
            self.label = label

    def set_units(self):
        if self.measurement is not None:
            self.units = self.measurement.representation()['units']
        else:
            self.units = None

    def to_representation(self):
        d = {'values': self.values, 'label': self.label, 'units': self.units}

        for i in self.representation_data:
            d = dict(list(d.items()) + list(i.items()))

        return d

    def add_dict_to_representation(self, d):
        if not isinstance(d, dict):
            msg = 'Only the dictionary can be added to the representation value'
            raise TypeError(msg)
        self.representation_data.append(d)

    def _get_mr_values_by_measurement(self, mrs):
        if self.measurement is None:
            return None

        return (
            mrs.filter(measurement=self.measurement)
            .order_by('serial')
            .values_list('value', flat=True)
        )

    @staticmethod
    def fill_none_axis(axes_dict):
        if axes_dict['axis_x']['values'] is None:
            max_len_axis_y = max([len(i['values']) for i in axes_dict['axis_y']])
            axes_dict['axis_x']['values'] = list(range(max_len_axis_y))
