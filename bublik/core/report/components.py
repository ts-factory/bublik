# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

import copy

from itertools import groupby

from bublik.core.report.services import args_sort, sequence_name_conversion, type_conversion


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

        # form the name of the y axis from the corresponding metas
        metas = {}
        for meta in mmr.measurement.metas.filter(
            type='measurement_subject',
            name__in=['name', 'type', 'base_units', 'multiplier'],
        ):
            metas[meta.name] = meta.value
        if 'name' in metas:
            self.axis_y = f"{metas['name']} ({metas['base_units']} * {metas['multiplier']})"
        else:
            self.axis_y = f"{metas['type']} ({metas['base_units']} * {metas['multiplier']})"

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
        self.id = self.label = self.build_id_and_label()
        self.sequence_group_arg = test_config['sequence_group_arg']
        self.axis_x_key = test_config['axis_x']
        self.axis_x_label = test_config['axis_x']
        self.axis_y_label = record_info[1]

        if table_view or chart_view:
            # group points into sequences
            sequences, string_args = self.get_sequences_and_str_args(record_points)
            dataset = self.create_dataset(sequences, test_config)

        if table_view:
            self.dataset_table = copy.deepcopy(dataset)
            self.percentage_calc(self.dataset_table[0], test_config)

        if chart_view:
            if string_args:
                self.warnings.append(
                    f'Invalid values \'{string_args}\' of the axis x argument for plotting '
                    'the chart. Chart building is skipped.',
                )
            else:
                self.dataset_chart = copy.deepcopy(dataset)

    def build_id_and_label(self):
        '''
        Build record id and label.
        '''
        label_list = []
        for _, val in self.args_vals.items():
            label_list.append(str(val))
        return '-'.join(label_list)

    def get_sequences_and_str_args(self, record_points):
        '''
        Group points into sequences. Get string arguments.
        '''
        sequences = {}
        string_args = set()
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
                    if type(arg) != int:
                        string_args.add(arg)
                    sequence[arg] = val
            sequences[sequence_group_arg_val] = sequence
        return sequences, string_args

    def create_dataset(self, sequences, test_config):
        '''
        Regroup points into datasets that are more convenient for UI.
        '''
        dataset = []
        for axis_x_val in list(sequences.values())[0]:
            dataset.append([axis_x_val])
        dataset_labels = [self.axis_x_key]
        for sequence_group_arg_val, sequence in sequences.items():
            dataset_labels.append(
                sequence_name_conversion(sequence_group_arg_val, test_config),
            )
            for i, point in enumerate(sequence.values()):
                dataset[i].append(point)
        dataset.insert(0, dataset_labels)
        return dataset

    def percentage_calc(self, dataset_labels, test_config):
        '''
        Calculate gain relative to "base" sequence.
        '''
        percentage_base_value = test_config['percentage_base_value']
        percentage_base_value = sequence_name_conversion(percentage_base_value, test_config)
        if percentage_base_value is not None:
            if percentage_base_value in dataset_labels:
                pbv_idx = dataset_labels.index(percentage_base_value)
                for dataset_label in dataset_labels[1:]:
                    if dataset_label != percentage_base_value:
                        self.dataset_table[0].append(f'{dataset_label} gain')
                        idx = dataset_labels.index(dataset_label)
                        for dataset_record in self.dataset_table[1:]:
                            if dataset_record[pbv_idx]:
                                percentage = round(
                                    100 * (dataset_record[idx] / dataset_record[pbv_idx] - 1),
                                    2,
                                )
                            else:
                                percentage = 0
                            dataset_record.append(percentage)
            else:
                self.warnings.append(
                    f'There is no sequence corresponding to the passed '
                    f'base value \'{percentage_base_value}\'. '
                    'Persentage calculation is skipped.',
                )


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
            try:
                test_point.args_vals = args_sort(
                    records_order,
                    test_point.args_vals,
                )
            except KeyError as incorrect_arg:
                msg = (
                    f'incorrect argument {incorrect_arg} in \'records_order\' for '
                    f'\'{test_name}\' test in the configuration. '
                    f'Check that {incorrect_arg} is not a sequence argument or an argument '
                    'that has the same value for all iterations.'
                )
                raise ValueError(msg) from incorrect_arg
        test_points = sorted(test_points, key=ReportPoint.points_grouper_records)

        for record_info, record_points in groupby(
            test_points,
            ReportPoint.points_grouper_records,
        ):
            record = ReportRecord(record_info, list(record_points), test_name, report_config)
            self.content.append(record.__dict__)
