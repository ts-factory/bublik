# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.forms.models import model_to_dict
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.report.components import ReportPoint, ReportTestLevel
from bublik.core.report.services import (
    filter_by_axis_y,
    filter_by_not_show_args,
    get_common_args,
)
from bublik.core.utils import convert_to_int_if_digit, unordered_group_by
from bublik.data.models import (
    Config,
    MeasurementResult,
    TestIterationResult,
)
from bublik.data.models.result import ResultType
from bublik.data.serializers import ConfigSerializer, TestIterationResultSerializer


__all__ = [
    'ReportViewSet',
]


class ReportViewSet(RetrieveModelMixin, GenericViewSet):
    queryset = TestIterationResult.objects.all()
    serializer_class = TestIterationResultSerializer

    @action(detail=True, methods=['get'])
    def configs(self, request, pk=None):
        '''
        Return a list of active configs that can be used to build a report on the current run.
        Request: GET /api/v2/report/<run_id>/configs
        '''
        run = self.get_object()
        iters = TestIterationResult.objects.filter(test_run=run)
        test_names = list(
            iters.filter(iteration__test__result_type=ResultType.conv(ResultType.TEST))
            .distinct('iteration__test__name')
            .values_list(
                'iteration__test__name',
                flat=True,
            ),
        )

        active_report_configs = Config.objects.filter(type='report', is_active=True)

        run_report_configs = []
        for report_config in active_report_configs:
            report_config_content = report_config.content
            # skip invalid
            if 'test_names_order' not in report_config_content:
                continue
            report_config_test_names = report_config_content['test_names_order']
            if set(report_config_test_names).intersection(test_names):
                run_report_configs.append(
                    model_to_dict(
                        report_config,
                        exclude=['type', 'is_active', 'user', 'content'],
                    ),
                )

        return Response({'run_report_configs': run_report_configs}, status=status.HTTP_200_OK)

    def retrieve(self, request, pk=None):
        r'''
        Request: GET /api/v2/report/<run_id>?config=<config_id\>
        '''
        warnings = []

        ### Get and check report config ###
        # check if the config ID has been passed
        report_config_id = request.query_params.get('config')
        if not report_config_id:
            msg = 'Report config wasn\'t passed'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValueError', 'message': msg},
            )

        # get config data
        report_config_obj = Config.objects.get(id=report_config_id)
        config_data = model_to_dict(
            report_config_obj,
            fields=['name', 'description', 'version'],
        )
        report_config = report_config_obj.content

        # check if the config content has the correct format
        serializer = ConfigSerializer(report_config_obj)
        serializer.validate_content(report_config)

        # get the session and the corresponding main package by ID
        result = self.get_object()
        main_pkg = result.root

        ### Get report points and unprocessed iterations ###

        mmrs_run = MeasurementResult.objects.filter(
            result__test_run=main_pkg,
        )

        # get common arguments by tests and filter measurement results by test configs
        common_args = {}
        mmrs_report = MeasurementResult.objects.none()
        for test_name, test_config in report_config['tests'].items():
            # filter measurement results by test name
            mmrs_test = mmrs_run.filter(result__iteration__test__name=test_name)
            if not mmrs_test:
                continue

            # filter measurement results by axis y
            axis_y = test_config['axis_y']
            mmrs_test = filter_by_axis_y(mmrs_test, axis_y)
            if not mmrs_test:
                msg = (
                    f'{test_name} test: no results after filtering by axis_y value. '
                    'Fix report configuration'
                )
                warnings.append(msg)
                continue

            # filter measurement results by not show params
            not_show_args = test_config['not_show_args']
            mmrs_test = filter_by_not_show_args(mmrs_test, not_show_args)
            if not mmrs_test:
                msg = (
                    f'{test_name} test: no results after filtering by not_show_args value. '
                    'Fix report configuration'
                )
                warnings.append(msg)
                if test_name in report_config['test_names_order']:
                    report_config['test_names_order'].remove(test_name)

            # collect arguments with the same value for all test iterations
            common_args[test_name] = get_common_args(mmrs_test)

            mmrs_report = mmrs_report.union(mmrs_test)

        mmrs_report = mmrs_report.order_by('id')

        # get points with data and unprocessed iterations
        points = []
        unprocessed_iters = []
        for mmr in mmrs_report:
            try:
                points.append(ReportPoint(mmr, common_args, report_config))
            except ValueError as ve:
                test_name = mmr.result.iteration.test.name
                common_test_args = common_args[test_name]
                invalid_iteration = {
                    'test_name': test_name,
                    'common_args': common_test_args,
                    'args_vals': {
                        arg.name: convert_to_int_if_digit(arg.value)
                        for arg in mmr.result.iteration.test_arguments.all()
                        if arg.name not in common_test_args
                    },
                    'reasons': ve.args[0],
                }
                if invalid_iteration not in unprocessed_iters:
                    unprocessed_iters.append(invalid_iteration)

        ### Group points into records ###
        content = []
        points_by_test_names = unordered_group_by(points, 'test_name')
        if report_config['test_names_order']:
            points_by_test_names = ReportPoint.by_test_name_sort(
                points_by_test_names,
                report_config['test_names_order'],
            )

        for test_name, test_points in points_by_test_names.items():
            test = ReportTestLevel(test_name, common_args, list(test_points), report_config)
            content.append(test.__dict__)

        report = {
            'warnings': warnings,
            'config': config_data,
            'content': content,
            'unprocessed_iters': unprocessed_iters,
        }

        return Response(data=report, status=status.HTTP_200_OK)
