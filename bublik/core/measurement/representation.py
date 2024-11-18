# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from collections import defaultdict
import contextlib
import copy
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
        self.subtitle = self.get_measurement_chart_label()

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
        self.dataset = sorted(self.dataset, key=lambda x: x['start'])
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
                    key=lambda x: x[merged_chart.axis_x['key']],
                )
            merged_charts.append(merged_chart)

        return merged_charts

    def get_measurement_chart_label(self, sequense_group_argument=None):
        measurement_data = self.measurement.representation()
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


class ReportRecordBuilder(ChartViewBuilder):
    '''
    Objects of this class contain the information necessary to display the passed points
    - the results of the passed measurement - on a chart and/or in a table in accordance
    with the passed configuration.
    '''

    REPR_KEYS: typing.ClassVar[list] = [
        'type',
        'id',
        'label',
        'chart',
        'table',
    ]

    def __init__(self, measurement, test_config, record_points):
        super().__init__(measurement)

        self.type = 'record-block'
        self.test_config = test_config
        sequences_config = test_config.get('sequences', {})
        series_arg_label = sequences_config.get(
            'arg_label', sequences_config.get('arg', None),
        )
        self.subtitle = self.get_measurement_chart_label(series_arg_label)

        axis_y = AxisRepresentationBuilder(measurement, key='y_value').to_representation()
        self.label = axis_y['label']

        table_view = self.test_config['table_view']
        chart_view = self.test_config['chart_view']
        if table_view or chart_view:
            axis_x_arg = self.test_config['axis_x']['arg']
            axis_x = AxisRepresentationBuilder(
                label=self.test_config['axis_x'].get('label', axis_x_arg),
                key='x_value',
            )
            arg_vals_labels = sequences_config.get('arg_vals_labels', None)
            sequences = self.get_sequences(record_points, arg_vals_labels)
            if chart_view:
                self.chart = ReportChartBuilder(
                    axis_x,
                    axis_y,
                    series_arg_label,
                    sequences,
                ).representation()
            if table_view:
                base_series_label = self.get_series_label(
                    sequences_config.get('percentage_base_value', None), arg_vals_labels,
                )
                self.table = ReportTableBuilder(
                    axis_x,
                    axis_y,
                    series_arg_label,
                    base_series_label,
                    sequences,
                ).representation()

    def representation(self):
        return {
            key: self.__dict__[key] for key in self.__class__.REPR_KEYS if key in self.__dict__
        }

    @staticmethod
    def group_by_subtitle(records):
        records_grouped = defaultdict(list)
        for record in records:
            records_grouped[record.subtitle].append(record)
        return records_grouped

    def get_series_label(self, sgav, arg_vals_labels):
        sgav = str(sgav)
        if arg_vals_labels:
            with contextlib.suppress(KeyError):
                sgav = str(arg_vals_labels[sgav])
        return sgav

    def get_sequences(self, record_points, arg_vals_labels):
        sequences = defaultdict(dict)
        for point in record_points:
            series_label = self.get_series_label(point.sequence_group_arg_val, arg_vals_labels)
            sequences[series_label].update(point.point)
        return sequences


class ReportRecordDataBuilder:
    '''
    This class allows you to get data for building tables and charts
    based on the passed sequences.
    '''
    def __init__(self, sequences):
        self.sequences = sequences

    def sort_points(self, sequences):
        return {sga: dict(sorted(points.items())) for sga, points in sequences.items()}

    def get_record_data(self):
        with contextlib.suppress(TypeError):
            self.sequences = self.sort_points(self.sequences)
        return [
            {
                'series': series_name,
                'points': [
                    {'x_value': axis_x_val} | point_data
                    for axis_x_val, point_data in sequence_points.items()
                ],
            } for series_name, sequence_points in self.sequences.items()
        ]


class ReportChartBuilder:
    '''
    This class describes the report chart.
    '''

    REPR_KEYS: typing.ClassVar[list] = [
        'warnings',
        'axis_x',
        'axis_y',
        'series_label',
        'data',
    ]

    def __init__(self, axis_x, axis_y, series_label, sequences):
        chart_sequences = self.get_chart_sequences(sequences)
        axis_x.add_values(self.get_axis_x_values(chart_sequences))
        self.complete_chart_sequences(axis_x, chart_sequences)
        self.axis_x = axis_x.to_representation()
        self.axis_y = axis_y
        self.series_label = series_label
        self.warnings = self.get_warnings(sequences)
        self.data = ReportRecordDataBuilder(chart_sequences).get_record_data()

    def representation(self):
        return {
            key: self.__dict__[key] for key in self.__class__.REPR_KEYS if key in self.__dict__
        }

    def get_axis_x_values(self, sequences):
        return sorted({axis_x for points in sequences.values() for axis_x in points})

    def get_chart_sequences(self, sequences):
        '''
        Leave only the points in the sequences that have the numeric value
        of the x-axis argument.
        '''
        return {
            sga: {x: point_data for x, point_data in points.items() if isinstance(x, int)}
            for sga, points in sequences.items()
        }

    def complete_chart_sequences(self, axis_x, chart_sequences):
        '''
        Align all sequences to the same x-axis by filling missing points with empty values.
        '''
        for sequence_arg, sequence in chart_sequences.items():
            for axis_x_val in set(axis_x.values) - set(sequence.keys()):
                chart_sequences[sequence_arg][axis_x_val] = {'y_value': None}

    def get_warnings(self, sequences):
        # points with a non-numeric value of the x-axis argument cannot be displayed on chart
        invalid_axis_x_values = list(
            set(self.get_axis_x_values(sequences))
            - set(self.axis_x['values']),
        )
        return [
            f'The results corresponding to {self.axis_x["label"]}={iaxv} '
            'cannot be displayed on the chart' for iaxv in invalid_axis_x_values
        ]


class ReportTableBuilder:
    '''
    This class describes the report table.
    '''

    REPR_KEYS: typing.ClassVar[list] = [
        'warnings',
        'formatters',
        'labels',
        'data',
    ]

    def __init__(self, axis_x, axis_y, series_label, base_series_label, sequences):
        self.warnings = []
        axis_x = axis_x.to_representation()
        self.labels = {
            axis_x['key']: axis_x['label'],
            axis_y['key']: axis_y['label'],
            'series': series_label,
        }
        self.data = ReportRecordDataBuilder(
            self.get_table_sequences(sequences, base_series_label),
        ).get_record_data()

    def representation(self):
        return {
            key: self.__dict__[key] for key in self.__class__.REPR_KEYS if key in self.__dict__
        }

    def sort_sequences(self, sequences, base_series_label):
        '''
        Make the base sequence first.
        '''
        return {
            base_series_label: sequences[base_series_label],
            **{k: v for k, v in sequences.items() if k != base_series_label},
        }

    def get_percentages(self, sequences, base_series_label):
        '''
        Calculate the gain relative to the base sequence.
        '''
        percentages = {}
        self.formatters = {}
        base_sequence = sequences.pop(base_series_label)
        for sgav, points in sequences.items():
            percentage_label = f'{sgav} gain'
            percentages[percentage_label] = {}
            self.formatters[percentage_label] = '%'
            for axis_x_val, point_data in points.items():
                try:
                    percentage = round(
                        100
                        * (
                            point_data['y_value'] / base_sequence[axis_x_val]['y_value'] - 1
                        ),
                        2,
                    )
                except ZeroDivisionError:
                    percentage = 'N/A'
                except KeyError:
                    continue
                percentages[percentage_label][axis_x_val] = {'y_value': percentage}
        return percentages

    def get_table_sequences(self, sequences, base_series_label):
        '''
        Add sequences with percentages.
        '''
        table_sequences = copy.deepcopy(sequences)
        if base_series_label != 'None':
            if base_series_label in sequences:
                table_sequences = self.sort_sequences(table_sequences, base_series_label)
                table_sequences.update(self.get_percentages(sequences, base_series_label))
            else:
                self.warnings.append(
                    f'There is no sequence corresponding to the passed '
                    f'base value {base_series_label}. '
                    'Persentage calculation is skipped.',
                )
        return table_sequences


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
