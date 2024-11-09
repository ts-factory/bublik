# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from itertools import groupby
import logging

from django.forms.models import model_to_dict
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.report.components import ReportPoint, ReportTestLevel
from bublik.core.report.services import (
    args_type_convesion,
    filter_by_axis_y,
    filter_by_not_show_args,
    get_common_args,
    get_unprocessed_iter_info,
)
from bublik.data.models import (
    Config,
    MeasurementResult,
    TestIterationResult,
)
from bublik.data.models.result import ResultType
from bublik.data.serializers import ConfigSerializer, TestIterationResultSerializer


logger = logging.getLogger('')


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

        ### Get record points and build axis names ###

        mmrs_run = MeasurementResult.objects.filter(
            result__test_run=main_pkg,
        )

        # get common arguments by tests and filter measurement results by test configs
        common_args = {}
        mmrs_report = MeasurementResult.objects.none()
        for test_name, test_config in report_config['tests'].items():
            # collect arguments with the same value for all test iterations
            common_args[test_name] = get_common_args(main_pkg, test_name)

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

            mmrs_report = mmrs_report.union(mmrs_test)

        mmrs_report = mmrs_report.order_by('id')

        # get points with data
        points = []
        unprocessed_iters = []
        for mmr in mmrs_report:
            point = ReportPoint(mmr, common_args, report_config)
            if not point.point or (
                point.sequence_group_arg and not point.sequence_group_arg_val
            ):
                unprocessed_iters.append(get_unprocessed_iter_info(point, common_args))
                continue
            points.append(point)

        ### Group points into records ###

        def by_test_name_sort(point_groups):
            sorted_point_groups = {}
            for test_name in report_config['test_names_order']:
                try:
                    sorted_point_groups[test_name] = point_groups[test_name]
                except KeyError:
                    continue
            return sorted_point_groups

        # group points into tests, and divide them into records and sequences
        test_records = []
        points = sorted(points, key=ReportPoint.points_grouper_tests)
        point_groups_by_test_name = dict(
            [test_name, list(points_group)]
            for test_name, points_group in groupby(points, ReportPoint.points_grouper_tests)
        )

        if report_config['test_names_order']:
            point_groups_by_test_name = by_test_name_sort(point_groups_by_test_name)

        # convert values of numeric arguments to int
        point_groups_by_test_name = args_type_convesion(point_groups_by_test_name)

        for test_name, test_points in point_groups_by_test_name.items():
            test = ReportTestLevel(test_name, common_args, list(test_points), report_config)
            test_records.append(test.__dict__)

        report = {
            'warnings': warnings,
            'config': config_data,
            'content': test_records,
            'unprocessed_iters': unprocessed_iters,
        }

        return Response(data=report, status=status.HTTP_200_OK)
