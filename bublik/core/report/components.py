# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import contextlib
import copy

from itertools import groupby

from bublik.core.report.services import (
    args_sort,
    build_axis_y_name,
    sequence_name_conversion,
    type_conversion,
)


'''
The report consists of blocks corresponding to the tests. Inside each of these blocks
there are records corresponding to a certain set of arguments. The points on each of the record
are combined into sequences according to the value of one specific argument and regrouded
into datasets according to axis x values (more convenient for UI).
'''


class ReportPoint:
    '''
    This class describes the points of records and the functions
    used to group them by tests, records, sequences and axix x value.
    '''

    def __init__(self, mmr, common_args, report_config):
        '''
        Build the point object based on the measurement result
        according to report config.
        '''
        self.test_name = mmr.result.iteration.test.name
        test_config = report_config['tests'][self.test_name]

        self.args_vals = {}
        self.axis_x = test_config['axis_x']
        self.axis_y = None
        self.sequence_group_arg = test_config['sequence_group_arg']
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
            elif arg.name not in common_args[self.test_name].keys():
                self.args_vals[arg.name] = arg.value

        # build the name of the y axis
        self.axis_y = build_axis_y_name(mmr)

    def points_grouper_tests(self):
        return self.test_name

    def points_grouper_records(self):
        return list(self.args_vals.values()), self.axis_y

    def points_grouper_sequences(self):
        return self.sequence_group_arg_val


class ReportRecord:
    '''
    This class describes the records.
    '''

    def __init__(self, record_info, record_points, test_name, report_config):
        test_config = report_config['tests'][test_name]
        table_view = test_config['table_view']
        chart_view = test_config['chart_view']

        self.type = 'record-entity'
        self.warnings = []
        self.args_vals = record_points[0].args_vals
        self.sequence_group_arg = test_config['sequence_group_arg']
        self.axis_x_key = test_config['axis_x']
        self.axis_x_label = test_config['axis_x']
        self.axis_y_label = record_info[1]
        self.label = self.build_label()

        id_tail = self.axis_y_label.replace(' ', '')
        self.id = f'{self.label}-{id_tail}'

        if table_view or chart_view:
            # group points into sequences
            sequences = self.get_sequences(record_points)
            # create datasets by sequences
            dataset_labels, dataset_data = self.create_dataset(sequences, test_config)

        if table_view:
            dataset_table_labels, dataset_table_data = self.percentage_calc(
                dataset_labels,
                dataset_data,
                test_config,
            )
            with contextlib.suppress(TypeError):
                dataset_table_data = sorted(dataset_table_data)
            self.dataset_table = [dataset_table_labels, *dataset_table_data]

        if chart_view:
            self.dataset_chart = []
            for dataset_item in dataset_data:
                # include in the chart dataset only those results that correspond
                # to the numeric values of the x-axis argument
                if type(dataset_item[0]) != int:
                    self.warnings.append(
                        f'The results corresponding to {dataset_labels[0]}={dataset_item[0]} '
                        'cannot be displayed on the chart',
                    )
                else:
                    self.dataset_chart.append(dataset_item)
            self.dataset_chart = [dataset_labels, *sorted(self.dataset_chart)]

    def build_label(self):
        '''
        Build record id and label.
        '''
        label_list = []
        for _, val in self.args_vals.items():
            label_list.append(str(val))
        return '-'.join(label_list)

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
                if k not in sequence.keys():
                    sequences[sequence_group_arg_val][k] = '-'

    def create_dataset(self, sequences, test_config):
        '''
        Regroup points into datasets that are more convenient for UI.
        '''
        self.complete_sequences(sequences)
        dataset_data = []
        for axis_x_val in list(sequences.values())[0]:
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

        percentage_base_value = test_config['percentage_base_value']
        percentage_base_value = sequence_name_conversion(percentage_base_value, test_config)

        if percentage_base_value is not None:
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
                    f'base value \'{percentage_base_value}\'. '
                    'Persentage calculation is skipped.',
                )

        return dataset_labels, dataset_data


class ReportTest:
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
        test_points = sorted(test_points, key=ReportPoint.points_grouper_records)

        for record_info, record_points in groupby(
            test_points,
            ReportPoint.points_grouper_records,
        ):
            record = ReportRecord(record_info, list(record_points), test_name, report_config)
            self.content.append(record.__dict__)
