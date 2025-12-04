# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from collections import defaultdict
import contextlib
import copy
import typing
from typing import TYPE_CHECKING

from bublik.core.run.data import is_result_unexpected
from bublik.core.utils import get_metric_prefix_units, key_value_transforming


if TYPE_CHECKING:
    from django.db.models import QuerySet

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

    def by_measurement_results(self, mmrs):
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
        for mmr in mmrs:
            point_data = mmr.representation()
            point_data.pop('sequence_number')
            point_data.update(
                {
                    'comments': mmr.measurement.representation()['comments'],
                    'has_error': is_result_unexpected(mmr.result),
                },
            )
            self.dataset.append(point_data)
        self.dataset = sorted(self.dataset, key=lambda x: x['start'])
        return self

    def set_merge_key_value(self, merge_mm_key: list[str]):
        '''
        Sets the value of the merge key based on the passed measurement attributes.
        '''
        measurement_data = self.measurement.representation()
        self.merge_key_value = [
            measurement_data[key] for key in measurement_data if key in merge_mm_key
        ]
        self.merge_key_value = [kv if kv is not None else '' for kv in self.merge_key_value]

    def get_measurement_chart_label(self, series_args_label=None):
        measurement_data = self.measurement.representation()
        label_items = {
            'type': measurement_data['type'],
            'units': measurement_data['units'],
            'aggr': measurement_data['aggr'],
            'tool': measurement_data['tool'],
            'keys': measurement_data['keys'],
        }

        units_aggr_data = ', '.join(
            label_items[key] for key in ['units', 'aggr'] if label_items[key]
        )

        label_parts = {
            'type': (
                (measurement_data['type'][0].upper() + measurement_data['type'][1:])
                if measurement_data['type']
                else None
            ),
            'units_arrg': f'({units_aggr_data})',
            'sgas': (f'by {series_args_label}' if series_args_label else None),
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

        series_config = test_config.get('overlay_by', [])
        series_args = [
            series_arg_config.get(
                'arg_label',
                series_arg_config['arg'],
            )
            for series_arg_config in series_config
        ]
        series_args_label = ', '.join(series_args)
        self.subtitle = self.get_measurement_chart_label(series_args_label)

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

            series_args_vals_labels = {
                series_arg_config['arg']: series_arg_config['arg_vals_labels']
                for series_arg_config in series_config
                if 'arg_vals_labels' in series_arg_config
            }

            series = self.get_series(record_points, series_args_vals_labels)
            if chart_view:
                self.chart = ReportChartBuilder(
                    axis_x,
                    axis_y,
                    series_args_label,
                    series,
                ).representation()
            if table_view:
                base_series_label = (
                    self.get_series_label(
                        {
                            series_arg_config['arg']: series_arg_config['percentage_base_value']
                            for series_arg_config in series_config
                        },
                        series_args_vals_labels,
                    )
                    if all(
                        'percentage_base_value' in series_arg_config
                        for series_arg_config in series_config
                    )
                    else None
                )
                self.table = ReportTableBuilder(
                    axis_x,
                    axis_y,
                    series_args_label,
                    base_series_label,
                    series,
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

    def get_series_label(self, series_args_vals, series_args_vals_labels):
        series_label_data = []
        for sga, sga_value in series_args_vals.items():
            sga_value = str(sga_value)
            if sga in series_args_vals_labels:
                with contextlib.suppress(KeyError):
                    sga_value = str(series_args_vals_labels[sga][sga_value])
            series_label_data.append(sga_value)
        return ', '.join(series_label_data)

    def get_series(self, record_points, series_args_vals_labels):
        series = defaultdict(dict)
        for point in record_points:
            series_label = self.get_series_label(
                point.series_args_vals,
                series_args_vals_labels,
            )
            series[series_label].update(point.point)
        return series


class ReportRecordDataBuilder:
    '''
    This class allows you to get data for building tables and charts
    based on the passed series.
    '''

    def __init__(self, series):
        self.series = series

    def sort_points_series(self):
        return {sga: dict(sorted(points.items())) for sga, points in self.series.items()}

    def normalize_series(self, axis_x_data):
        '''
        Align all series to the same x-axis by filling missing points with empty values.
        '''
        for series_arg, series in self.series.items():
            for axis_x_val in set(axis_x_data.get('values', [])) - set(series.keys()):
                self.series[series_arg][axis_x_val] = {'y_value': None}

    def get_record_data(self, axis_x_data):
        self.normalize_series(axis_x_data)
        with contextlib.suppress(TypeError):
            self.series = self.sort_points_series()
        return [
            {
                'series': series_name,
                'points': [
                    {'x_value': axis_x_val} | point_data
                    for axis_x_val, point_data in series_points.items()
                ],
            }
            for series_name, series_points in self.series.items()
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

    def __init__(self, axis_x, axis_y, series_label, series):
        chart_series = self.get_chart_series(series)
        axis_x.add_values(sorted(self.get_axis_x_values(chart_series)))
        self.axis_x = axis_x.to_representation()
        self.axis_y = axis_y
        self.series_label = series_label
        self.warnings = self.get_warnings(series)
        self.data = ReportRecordDataBuilder(chart_series).get_record_data(self.axis_x)

    def representation(self):
        return {
            key: self.__dict__[key] for key in self.__class__.REPR_KEYS if key in self.__dict__
        }

    def get_axis_x_values(self, series):
        return {axis_x for points in series.values() for axis_x in points}

    def get_chart_series(self, series):
        '''
        Leave only the points in the series that have the numeric value
        of the x-axis argument.
        '''
        return {
            sga: {
                x: point_data for x, point_data in points.items() if isinstance(x, (int, float))
            }
            for sga, points in series.items()
        }

    def get_warnings(self, series):
        # points with a non-numeric value of the x-axis argument cannot be displayed on chart
        invalid_axis_x_values = list(
            set(self.get_axis_x_values(series)) - set(self.axis_x.get('values', [])),
        )
        return [
            f'The results corresponding to {self.axis_x["label"]}={iaxv} '
            'cannot be displayed on the chart'
            for iaxv in invalid_axis_x_values
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

    def __init__(self, axis_x, axis_y, series_label, base_series_label, series):
        self.warnings = []
        axis_x = axis_x.to_representation()
        self.labels = {
            axis_x['key']: axis_x['label'],
            axis_y['key']: axis_y['label'],
            'series': series_label,
        }
        self.data = ReportRecordDataBuilder(
            self.get_table_series(series, base_series_label),
        ).get_record_data(axis_x)

    def representation(self):
        return {
            key: self.__dict__[key] for key in self.__class__.REPR_KEYS if key in self.__dict__
        }

    def sort_series(self, series, base_series_label):
        '''
        Make the base series first.
        '''
        return {
            base_series_label: series[base_series_label],
            **{k: v for k, v in series.items() if k != base_series_label},
        }

    def get_percentages(self, series, base_series_label):
        '''
        Calculate the gain relative to the base series.
        '''
        percentages = {}
        self.formatters = {}
        base_series = series.pop(base_series_label)
        for sgav, points in series.items():
            percentage_label = f'{sgav} gain'
            percentages[percentage_label] = {}
            self.formatters[percentage_label] = '%'
            for axis_x_val, point_data in points.items():
                try:
                    percentage = round(
                        100 * (point_data['y_value'] / base_series[axis_x_val]['y_value'] - 1),
                        2,
                    )
                except ZeroDivisionError:
                    percentage = 'N/A'
                except KeyError:
                    continue
                percentages[percentage_label][axis_x_val] = {'y_value': percentage}
        return percentages

    def get_table_series(self, series, base_series_label):
        '''
        Add series with percentages.
        '''
        table_series = copy.deepcopy(series)
        if base_series_label:
            if base_series_label in series:
                table_series = self.sort_series(table_series, base_series_label)
                table_series.update(self.get_percentages(series, base_series_label))
            else:
                self.warnings.append(
                    f'There is no series corresponding to the passed '
                    f'base value {base_series_label}. '
                    'Persentage calculation is skipped.',
                )
        return table_series


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
