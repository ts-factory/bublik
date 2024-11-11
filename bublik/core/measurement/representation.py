# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import groupby
import typing

from django.db.models import QuerySet

from bublik.core.measurement.services import get_measurement_results
from bublik.core.utils import get_metric_prefix_units, key_value_transforming
from bublik.data.models import ChartView, Measurement, MeasurementResultList, View


class ChartViewBuilder:
    '''
    Considering the purpose of ChartView model to describe views of two measurements
    on a single graph ChartViewBuilder provides the interface to do this.
    '''

    REPR_KEYS: typing.ClassVar[list] = [
        'id',
        'title',
        'subtitle',
        'axis_x',
        'axis_y',
        'dataset',
    ]

    def __init__(self, y_measurement: Measurement, view: View = None):
        self.measurement = y_measurement
        measurement_data = self.measurement.representation()
        self.id = measurement_data['measurement_id']
        self.title = view.representation()['title'] if view else None
        self.subtitle = self.get_measurement_chart_label(measurement_data)

    def convert_dataset(self):
        '''
        Convert the chart dataset from a dictionary list to a list of lists
        (more convinient for UI).
        '''
        keys = next(iter(self.dataset)).keys()
        self.dataset = [point.values() for point in self.dataset]
        self.dataset.insert(0, keys)

    def representation(self):
        if all(isinstance(point, dict) for point in self.dataset):
            self.convert_dataset()
        return {key: self.__dict__[key] for key in self.__class__.REPR_KEYS}

    def by_lines(
        self,
        y_meas_res_list: MeasurementResultList,
        x_meas_res_list: MeasurementResultList = None,
    ):
        '''
        This method allows you to obtain data for plotting the dependence of the Y measurement
        results on the X measurement results (on the ordinal number of the measurement value
        in the sequence of results in the absence of measurement X).
        '''
        axis_x_measurement = x_meas_res_list.measurement if x_meas_res_list else None
        self.axis_x = AxisRepresentationBuilder(axis_x_measurement).to_representation()
        self.axis_y = AxisRepresentationBuilder(self.measurement).to_representation()

        y_values = y_meas_res_list.representation()['value']
        x_values = x_meas_res_list.value if x_meas_res_list else list(range(len(y_values)))
        self.dataset = [[x, y] for x, y in zip(x_values, y_values)]
        self.dataset.insert(0, [self.axis_x['key'], self.axis_y['key']])

        return self

    @classmethod
    def by_points(cls, cvs: QuerySet[ChartView]):
        # 1. Check dots have the same measurements
        # 2. measurement = cvs[0].measurement
        # 3. iteration_results = cvs.values_list('result', flat=True)
        # 4. measurement_results = get_measurement_results(iteration_results, measurement)
        # 5. view = create manually
        # 6. return cls(view, measurement, measurement_results)
        pass

    def by_measurement_results(self, result_ids):
        '''
        This method allows you to obtain data for plotting changes in measurement
        results over iterations from the passed range. In addition to the values,
        each point also contains the IDs of the run, result, and iteration in order
        to be able to switch to other views.
        '''
        self.axis_x = AxisRepresentationBuilder(
            label='Start of measurement test',
            key='start',
        ).to_representation()
        self.axis_y = AxisRepresentationBuilder(
            measurement=self.measurement,
            key='value',
        ).to_representation()
        self.dataset = []
        mmrs = get_measurement_results(result_ids, self.measurement)
        for mmr in mmrs:
            point_data = mmr.representation()
            point_data.pop('sequence_number')
            self.dataset.append(point_data)
        return self

    def set_merge_key_value(self, merge_mm_key: typing.List[str]):
        '''
        Sets the value of the merge key based on the passed measurement attributes.
        '''
        measurement_data = self.measurement.representation()
        self.merge_key_value = [
            measurement_data[key] for key in measurement_data if key in merge_mm_key
        ]
        self.merge_key_value = [kv if kv is not None else '' for kv in self.merge_key_value]

    @staticmethod
    def merge_charts_by(charts, merge_mm_key: typing.List[str]):
        '''
        Merges the passed charts by the values of the passed measurement attributes.
        '''
        # get "per point" measurement attributes
        per_point_attrs = [
            attr
            for attr in next(iter(charts)).measurement.representation()
            if attr not in merge_mm_key
        ]
        # prepare charts for merging
        for chart in charts:
            # set the merge key value
            chart.set_merge_key_value(merge_mm_key)
            # update points with "per point" measurement data
            per_point_measurement_data = {
                attr: value
                for attr, value in chart.measurement.representation().items()
                if attr in per_point_attrs
            }
            for point_data in chart.dataset:
                point_data.update(per_point_measurement_data)

        # group charts by merge key
        charts = sorted(charts, key=lambda chart: chart.merge_key_value)
        chart_groups = [
            list(group) for _, group in groupby(charts, key=lambda chart: chart.merge_key_value)
        ]

        # get merged charts
        merged_charts = []
        for chart_group in chart_groups:
            merged_chart = next(iter(chart_group))
            if len(chart_group) > 1:
                # get merged dataset
                for chart in chart_group[1:]:
                    merged_chart.dataset.extend(chart.dataset)
                # sort merged dataset
                merged_chart.dataset = sorted(
                    merged_chart.dataset,
                    key=lambda x: x[merged_chart.axis_x_key],
                )
            merged_charts.append(merged_chart)

        return merged_charts

    @staticmethod
    def get_measurement_chart_label(measurement_data, sequense_group_argument=None):
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
            'keys': f'({(", ".join(label_items["keys"]))})' if label_items['keys'] else None,
        }

        return ' '.join(
            [label_part for label_part in label_parts.values() if label_part],
        ).replace(' :', ':')


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
    def __init__(self, measurement=None, label=None, key=None):
        self.measurement = measurement
        self.label = self.get_label(label)
        self.key = self.get_key(key)
        self.values = []

    def get_label(self, label):
        if label is not None:
            return label
        if self.measurement is None:
            return 'Sequence number'
        measurement_data = self.measurement.representation()
        label = (
            measurement_data['name'] if measurement_data['name'] else measurement_data['type']
        )
        if measurement_data['units']:
            label += f' ({measurement_data["units"]})'
        return label

    def get_key(self, key):
        if key is not None:
            return key
        return self.label

    def add_values(self, values):
        self.values = values

    def to_representation(self):
        axis_data = {'label': self.label, 'key': self.key}
        if self.values:
            axis_data.update({'values': self.values})
        return axis_data

    def set_units(self):
        if self.measurement is not None:
            self.units = self.measurement.representation()['units']
        else:
            self.units = None

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
