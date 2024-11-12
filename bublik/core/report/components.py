# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from itertools import groupby

from bublik.core.measurement.representation import ReportRecordBuilder
from bublik.core.report.services import type_conversion


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
    This class describes the points of records and the functions
    used to group them by tests, agruments, measurements, records and sequences.
    '''

    def __init__(self, mmr, common_args, report_config):
        '''
        Build the point object based on the measurement result
        according to report config.
        '''
        # get test level data
        self.test_name = mmr.result.iteration.test.name

        # get argument values level data
        self.args_vals = {}

        # get sequences level data
        self.test_config = report_config['tests'][self.test_name]
        self.sequence_group_arg = self.test_config.get('sequences', {}).get('arg', None)
        self.sequence_group_arg_val = None

        # get measurements level data
        self.measurement_id = mmr.measurement.id
        self.measurement = mmr.measurement

        # get x-axis data
        self.axis_x_arg = self.test_config['axis_x']['arg']
        axis_x_value = None
        value = None

        # collect test argument values, value of sequence argument and the point
        for arg in mmr.result.iteration.test_arguments.all():
            if arg.name == self.axis_x_arg:
                axis_x_value = type_conversion(arg.value)
                value = mmr.value
            elif arg.name == self.sequence_group_arg:
                self.sequence_group_arg_val = type_conversion(arg.value)
            elif arg.name not in common_args[self.test_name]:
                self.args_vals[arg.name] = type_conversion(arg.value)

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
                    'result_id': mmr.id,
                },
            },
        }

    def points_grouper_tests(self):
        return self.test_name

    def points_grouper_args_vals(self):
        return list(self.args_vals.values())

    def points_grouper_measurements(self):
        return self.measurement_id

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

        records = []
        for _measurement_id, measurement_points in groupby(
            arg_val_record_points,
            ReportPoint.points_grouper_measurements,
        ):
            points = measurement_points
            records.append(
                ReportRecordBuilder(
                    next(iter(points)).measurement,
                    report_config['tests'][test_name],
                    points,
                ),
            )

        # group measurement records by measurement chart labels
        records_by_subtitles = ReportRecordBuilder.group_by_subtitle(records)
        for subtitle, records in records_by_subtitles.items():
            self.content.append(ReportMeasurementLevel(self.id, subtitle, records).__dict__)

    def build_label(self):
        '''
        Build arg-val block label.
        '''
        label_list = []
        for arg, val in self.args_vals.items():
            label_list.append(f'{arg}: {val}')
        return ' | '.join(label_list)


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
