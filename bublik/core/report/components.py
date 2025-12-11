# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from bublik.core.measurement.representation import ReportRecordBuilder
from bublik.core.run.data import is_result_unexpected
from bublik.core.utils import parse_number, unordered_group_by


'''
The report has four levels of nesting:
1. Test level. It contains test common arguments and enabled views.
2. Arguments values level. It contains arguments with their values.
3. Measurement level. It contains measurement (combination of type, aggregation, keys and
   units of measurement).
4. Record level. It contains records (tables and/or charts with measurement results).
   The points on each of the record are combined into sequences according to the value of one
   specific argument.
'''


class ReportPoint:
    '''
    This class describes the points of records and the function that allows you to group
    them by the value of the passed attribute.
    '''

    def __init__(self, mmr, common_args, report_config):
        '''
        Build the point object based on the measurement result
        according to report config.
        '''
        # get test level data
        self.test_name = mmr.result.iteration.test.name
        self.test_config = report_config['tests'][self.test_name]

        # get argument values level data
        self.args_vals = {}

        # get sequences level data
        sequence_group_arg = self.test_config.get('sequences', {}).get('arg', None)
        self.sequence_group_arg_val = None

        # get measurements level data
        self.measurement = mmr.measurement
        self.measurement_key = mmr.measurement_group_key

        # get x-axis data
        self.axis_x_arg = self.test_config['axis_x']['arg']
        axis_x_value = None
        value = None

        # collect test argument values, value of sequence argument and the point
        for arg in mmr.result.iteration.test_arguments.all():
            if arg.name == self.axis_x_arg:
                axis_x_value = parse_number(arg.value)
                value = mmr.value
            elif arg.name == sequence_group_arg:
                self.sequence_group_arg_val = parse_number(arg.value)
            elif arg.name not in common_args[self.test_name]:
                self.args_vals[arg.name] = parse_number(arg.value)

        # check iteration
        warnings = []
        if axis_x_value is None:
            warnings.append(f'The test has no argument {self.axis_x_arg}')
        if sequence_group_arg is not None and self.sequence_group_arg_val is None:
            warnings.append(f'The test has no argument {sequence_group_arg}')
        if warnings:
            raise ValueError(warnings)

        # handle argument values (sort and build label)
        self.sort_arg_vals()
        self.arg_val_label = ' | '.join(
            [f'{arg}: {val}' for arg, val in self.args_vals.items()],
        )

        # set point
        self.point = {
            axis_x_value: {
                'y_value': value,
                'metadata': {
                    'iteration_id': mmr.result.iteration.id,
                    'result_id': mmr.result.id,
                    'has_error': is_result_unexpected(mmr.result),
                },
            },
        }

    def sort_arg_vals(self):
        '''
        Sort object argument values according to the configuration.
        '''
        records_order = self.test_config['records_order']
        if self.test_config['records_order']:
            args_vals_sorted = {
                arg: self.args_vals[arg] for arg in records_order if arg in self.args_vals
            }
            args_vals_sorted.update(self.args_vals)
            self.args_vals = args_vals_sorted
        else:
            self.args_vals = dict(sorted(self.args_vals.items()))

    @staticmethod
    def by_test_name_sort(by_test_name_points, ordered_test_names):
        '''
        Return groups of points by name, sorted according to the configuration.
        '''
        by_test_name_points_sorted = {
            test_name: by_test_name_points[test_name]
            for test_name in ordered_test_names
            if test_name in by_test_name_points
        }
        by_test_name_points_sorted.update(by_test_name_points)
        return by_test_name_points_sorted


class ReportMeasurementLevel:
    '''
    This class describes the report blocks corresponding to the measurements.
    '''

    def __init__(
        self,
        av_lvl_id,
        subtitle,
        records,
    ):
        self.type = 'measurement-block'
        self.label = subtitle
        self.id = '_'.join([av_lvl_id, '-'.join([str(record.id) for record in records])])
        self.content = []

        for record in records:
            record_data = record.representation()
            record_data.update({'id': '_'.join([self.id, str(record.id)])})
            self.content.append(record_data)


class ReportArgsValsLevel:
    '''
    This class describes the report blocks corresponding to the test arguments
    and their values.
    '''

    def __init__(self, test_name, test_lvl_id, arg_val_label, arg_val_points, report_config):
        test_config = report_config['tests'][test_name]

        self.type = 'arg-val-block'
        self.args_vals = arg_val_points[0].args_vals
        self.label = arg_val_label
        self.id = '_'.join([test_lvl_id, self.label.replace(' ', '')])
        self.content = []

        # create measurement records
        records = []
        points_by_measurements = unordered_group_by(arg_val_points, 'measurement_key')
        for _measurement_key, points in points_by_measurements.items():
            records.append(
                ReportRecordBuilder(
                    next(iter(points)).measurement,
                    test_config,
                    points,
                ),
            )

        # group measurement records by measurement chart labels
        records_by_subtitles = ReportRecordBuilder.group_by_subtitle(records)
        for subtitle, records in records_by_subtitles.items():
            self.content.append(ReportMeasurementLevel(self.id, subtitle, records).__dict__)

        # move the sequences argument alongside the other arguments if it has the same value
        # across all results within the corresponding block
        sequences_config = test_config.get('sequences', None)
        if sequences_config and test_config['chart_view']:
            series_label = self.content[0]['content'][0]['chart']['series_label']
            series_val = self.content[0]['content'][0]['chart']['data'][0]['series']
            if all(
                len(rec_block['chart']['data']) == 1
                and rec_block['chart']['data'][0]['series'] == series_val
                for meas_block in self.content
                for rec_block in meas_block['content']
            ):
                series_param = f'{series_label}: {series_val}'
                arg_val_id = '|'.join([self.id, series_param.replace(' ', '')])
                for meas_block in self.content:
                    meas_id = arg_val_id + meas_block['id'][len(self.id) :]
                    for rec_block in meas_block['content']:
                        rec_block['id'] = meas_id + rec_block['id'][len(meas_block['id']) :]
                        rec_block['chart'].pop('series_label')
                        rec_block['chart']['data'][0].pop('series')
                        if 'table' in rec_block:
                            rec_block['table']['data'][0].pop('series')
                            rec_block['table']['labels'].pop('series')
                    meas_block['id'] = meas_id

                self.args_vals.update(
                    {
                        series_label: parse_number(series_val),
                    },
                )
                self.id = arg_val_id
                self.label = f'{self.label} | {series_param}'


class ReportTestLevel:
    '''
    This class describes the report blocks corresponding to the tests.
    '''

    def __init__(self, test_name, common_args, test_points, report_config):
        test_config = report_config['tests'][test_name]

        self.type = 'test-block'
        self.id = test_name
        self.label = test_name
        self.enable_table_view = test_config['table_view']
        self.enable_chart_view = test_config['chart_view']
        self.common_args = common_args[test_name]
        self.content = []

        points_by_argvals = unordered_group_by(test_points, 'arg_val_label')

        for arg_val_label, points in points_by_argvals.items():
            arg_val_record = ReportArgsValsLevel(
                test_name,
                self.id,
                arg_val_label,
                points,
                report_config,
            )
            self.content.append(arg_val_record.__dict__)
