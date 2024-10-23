# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib
import copy

from itertools import groupby

from bublik.core.report.services import (
    args_sort,
    get_meas_label_and_name,
    sequence_name_conversion,
    type_conversion,
)


'''
The report has four levels of nesting:
1. Test level. It contains test common arguments and enabled views.
2. Arguments values level. It contains arguments with their values.
3. Measurement level. It contains measurement (combination of type, aggregation, keys and
   units of measurement).
4. Record level. It contains records (tables and/or charts with measurement results).
   The points on each of the record are combined into sequences according to the value of one
   specific argument and regrouped into datasets according to axis x values
   (more convenient for UI).
'''


class ReportPoint:
    '''
    This class describes the points of records and the functions
    used to group them by tests, agruments, measurements, records and sequences.
    '''

    def __init__(self, mmr, common_args, report_config):
        '''
        Build the point object based on the measurement result
        according to report config.
        '''
        self.test_name = mmr.result.iteration.test.name
        test_config = report_config['tests'][self.test_name]

        self.args_vals = {}
        self.axis_x = test_config['axis_x']['arg']
        sequences = test_config.get('sequences', {})
        self.sequence_group_arg = sequences.get('arg', None)
        sequence_group_arg_label = sequences.get('arg_label', None)
        self.sequence_group_arg_val = None
        self.point = {}

        # collect test arguments values, value of sequence argument and the point
        for arg in mmr.result.iteration.test_arguments.all():
            if arg.name == self.axis_x:
                self.point[type_conversion(arg.value)] = mmr.value
            elif arg.name == self.sequence_group_arg:
                # ID is needed to maintain order when sorting before
                # it is grouped in the future
                self.sequence_group_arg_val = (
                    arg.id,
                    sequence_name_conversion(arg.value, test_config),
                )
            elif arg.name not in common_args[self.test_name]:
                self.args_vals[arg.name] = arg.value

        # build the record label and the name of the y axis
        self.measurement_label, self.measurement_name = get_meas_label_and_name(
            mmr,
            sequence_group_arg_label,
        )

    def points_grouper_tests(self):
        return self.test_name

    def points_grouper_args_vals(self):
        return list(self.args_vals.values())

    def points_grouper_measurements(self):
        return self.measurement_label

    def points_grouper_records(self):
        return self.measurement_name

    def points_grouper_sequences(self):
        return self.sequence_group_arg_val


class ReportRecordLevel:
    '''
    This class describes the report blocks corresponding to the records (charts and/or tables).
    '''

    def __init__(self, test_name, meas_lvl_id, record_info, record_points, report_config):
        test_config = report_config['tests'][test_name]
        table_view = test_config['table_view']
        chart_view = test_config['chart_view']

        self.type = 'record-block'
        self.warnings = []
        self.multiple_sequences = bool('sequences' in test_config)

        self.axis_x_label = test_config['axis_x'].get(
            'label',
            test_config['axis_x']['arg'],
        )

        if self.multiple_sequences:
            sequence_group_arg_label = test_config['sequences'].get(
                'arg_label',
                test_config['sequences']['arg'],
            )
            self.axis_x_key = f'{self.axis_x_label} / {sequence_group_arg_label}'
        else:
            self.axis_x_key = f'{self.axis_x_label}'

        self.axis_y_label = record_info
        self.id = f'{meas_lvl_id}_{self.axis_y_label}'

        if table_view or chart_view:
            # create datasets
            if not self.multiple_sequences:
                dataset_labels = [self.axis_x_key, self.axis_y_label]
                dataset_data = []
                for record_point in list(record_points):
                    for arg, val in record_point.point.items():
                        dataset_data.append([arg, val])
                with contextlib.suppress(TypeError):
                    dataset_data = sorted(dataset_data)
            else:
                # group points into sequences
                sequences = self.get_sequences(record_points)
                # create datasets by sequences
                dataset_labels, dataset_data = self.create_dataset(sequences, test_config)
        if table_view:
            self.dataset_table = self.get_dataset_table(
                dataset_labels,
                dataset_data,
                test_config,
            )
        if chart_view:
            self.dataset_chart = self.get_dataset_chart(dataset_labels, dataset_data)

    def get_sequences(self, record_points):
        '''
        Group points into sequences.
        '''
        sequences = {}
        record_points = sorted(record_points, key=ReportPoint.points_grouper_sequences)
        for seq_arg_id_and_val, sequence_points in groupby(
            record_points,
            ReportPoint.points_grouper_sequences,
        ):
            sequence_group_arg_val = seq_arg_id_and_val[1]
            # add points to the corresponding sequence
            sequence = {}
            for sequence_point in list(sequence_points):
                for arg, val in sequence_point.point.items():
                    sequence[arg] = val
            sequences[sequence_group_arg_val] = sequence
        return sequences

    def complete_sequences(self, sequences):
        axis_x_vals = set()
        for _, sequence in sequences.items():
            for k in sequence:
                axis_x_vals.add(k)

        for sequence_group_arg_val, sequence in sequences.items():
            for k in axis_x_vals:
                if k not in sequence:
                    sequences[sequence_group_arg_val][k] = '-'

    def create_dataset(self, sequences, test_config):
        '''
        Regroup points into datasets that are more convenient for UI.
        '''
        self.complete_sequences(sequences)
        dataset_data = []
        for axis_x_val in next(iter(sequences.values())):
            dataset_data.append([axis_x_val])
        dataset_labels = [self.axis_x_key]
        for sequence_group_arg_val, sequence in sequences.items():
            dataset_labels.append(
                sequence_name_conversion(sequence_group_arg_val, test_config),
            )
            for i, point in enumerate(sequence.values()):
                dataset_data[i].append(point)
        return dataset_labels, dataset_data

    def percentage_calc(self, dataset_labels, dataset_data, test_config):
        '''
        Calculate gain relative to "base" sequence.
        '''
        dataset_labels = copy.deepcopy(dataset_labels)
        dataset_data = copy.deepcopy(dataset_data)

        percentage_base_value = test_config['sequences']['percentage_base_value']
        percentage_base_value = sequence_name_conversion(percentage_base_value, test_config)

        if percentage_base_value in dataset_labels:
            self.formatters = {}
            pbv_idx = dataset_labels.index(percentage_base_value)
            for dataset_label in dataset_labels[1:]:
                if dataset_label != percentage_base_value:
                    dataset_label_gain = f'{dataset_label} gain'
                    dataset_labels.append(dataset_label_gain)
                    self.formatters[dataset_label_gain] = '%'
                    idx = dataset_labels.index(dataset_label)
                    for dataset_item in dataset_data:
                        try:
                            percentage = round(
                                100 * (dataset_item[idx] / dataset_item[pbv_idx] - 1),
                                2,
                            )
                        except ZeroDivisionError:
                            percentage = 'na'
                        except TypeError:
                            percentage = '-'
                        dataset_item.append(percentage)
        else:
            self.warnings.append(
                f'There is no sequence corresponding to the passed '
                f'base value {percentage_base_value}. '
                'Persentage calculation is skipped.',
            )

        return dataset_labels, dataset_data

    def get_dataset_table(self, dataset_labels, dataset_data, test_config):
        if self.multiple_sequences and 'percentage_base_value' in test_config['sequences']:
            dataset_labels, dataset_data = self.percentage_calc(
                dataset_labels,
                dataset_data,
                test_config,
            )
        with contextlib.suppress(TypeError):
            dataset_data = sorted(dataset_data)
        return [dataset_labels, *dataset_data]

    def get_dataset_chart(self, dataset_labels, dataset_data):
        dataset_chart_data = []
        for dataset_item in dataset_data:
            # include in the chart dataset only those results that correspond
            # to the numeric values of the x-axis argument
            if not isinstance(dataset_item[0], int):
                self.warnings.append(
                    f'The results corresponding to {dataset_labels[0]}={dataset_item[0]} '
                    'cannot be displayed on the chart',
                )
            else:
                dataset_chart_data.append(dataset_item)
        return [dataset_labels, *sorted(dataset_chart_data)]


class ReportMeasurementLevel:
    '''
    This class describes the report blocks corresponding to the measurements.
    '''

    def __init__(
        self,
        test_name,
        av_lvl_id,
        measurement_info,
        measurement_points,
        report_config,
    ):
        self.type = 'measurement-block'
        self.label = measurement_info
        self.id = '_'.join([av_lvl_id, self.label.replace(' ', '')])
        self.content = []

        measurement_points = sorted(
            measurement_points,
            key=ReportPoint.points_grouper_records,
        )

        for record_info, record_points in groupby(
            measurement_points,
            ReportPoint.points_grouper_records,
        ):
            record = ReportRecordLevel(
                test_name,
                self.id,
                record_info,
                list(record_points),
                report_config,
            )
            self.content.append(record.__dict__)


class ReportArgsValsLevel:
    '''
    This class describes the report blocks corresponding to the test arguments
    and their values.
    '''

    def __init__(self, test_name, test_lvl_id, arg_val_record_points, report_config):
        self.type = 'arg-val-block'
        self.args_vals = arg_val_record_points[0].args_vals
        self.label = self.build_label()
        self.id = '_'.join([test_lvl_id, self.label.replace(' ', '')])
        self.content = []

        arg_val_record_points = sorted(
            arg_val_record_points,
            key=ReportPoint.points_grouper_measurements,
        )

        for measurement_info, measurement_points in groupby(
            arg_val_record_points,
            ReportPoint.points_grouper_measurements,
        ):
            measurement_record = ReportMeasurementLevel(
                test_name,
                self.id,
                measurement_info,
                list(measurement_points),
                report_config,
            )
            self.content.append(measurement_record.__dict__)

    def build_label(self):
        '''
        Build arg-val block label.
        '''
        label_list = []
        for _, val in self.args_vals.items():
            label_list.append(str(val))
        return '-'.join(label_list)


class ReportTestLevel:
    '''
    This class describes the report blocks corresponding to the tests.
    '''

    def __init__(self, test_name, common_args, test_points, report_config):
        test_config = report_config['tests'][test_name]
        records_order = test_config['records_order']

        self.type = 'test-block'
        self.id = test_name
        self.label = test_name
        self.enable_table_view = test_config['table_view']
        self.enable_chart_view = test_config['chart_view']
        self.common_args = common_args[test_name]
        self.content = []

        for test_point in test_points:
            test_point.args_vals = args_sort(
                records_order,
                test_point.args_vals,
            )
        test_points = sorted(test_points, key=ReportPoint.points_grouper_args_vals)

        for _, arg_val_record_points in groupby(
            test_points,
            ReportPoint.points_grouper_args_vals,
        ):
            arg_val_record = ReportArgsValsLevel(
                test_name,
                self.id,
                list(arg_val_record_points),
                report_config,
            )
            self.content.append(arg_val_record.__dict__)
