# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from itertools import groupby
import logging

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.mixins import RetrieveModelMixin
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from bublik.core.meta.match_references import build_revision_references
from bublik.core.report.components import ReportPoint, ReportTestLevel
from bublik.core.report.services import (
    args_type_convesion,
    build_report_title,
    check_report_config,
    filter_by_axis_y,
    filter_by_not_show_args,
    get_common_args,
    get_report_config,
    get_report_config_by_id,
)
from bublik.core.run.external_links import get_sources
from bublik.core.shortcuts import build_absolute_uri
from bublik.data.models import MeasurementResult, Meta, TestIterationResult
from bublik.data.serializers import TestIterationResultSerializer


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
        Return a list of configs that can be used to build a report on the current run.
        Route: /api/v2/report/<run_id>/configs/
        '''
        run = self.get_object()
        iters = TestIterationResult.objects.filter(test_run=run)
        test_names = list(
            iters.distinct('iteration__test__name').values_list(
                'iteration__test__name',
                flat=True,
            ),
        )

        run_report_configs = []
        invalid_report_config_files = []
        for report_config_file, report_config in get_report_config():
            if report_config:
                try:
                    report_config_test_names = report_config['test_names_order']
                    # check whether the sets of run tests and config tests overlap
                    if set(report_config_test_names).intersection(test_names):
                        run_report_configs.append(
                            {
                                'id': report_config['id'],
                                'name': report_config['name'],
                                'description': report_config['description'],
                            },
                        )
                except KeyError as ke:
                    invalid_report_config_files.append(
                        {
                            'file': report_config_file,
                            'reason': f'Invalid format: the key {ke} is missing',
                        },
                    )
            else:
                invalid_report_config_files.append(
                    {
                        'file': report_config_file,
                        'reason': 'Invalid format: JSON is expected',
                    },
                )

        data = {
            'run_report_configs': run_report_configs,
            'invalid_report_config_files': invalid_report_config_files,
        }

        return Response(data=data)

    def retrieve(self, request, pk=None):
        ### Get and check report config ###
        # check if the config ID has been passed
        report_config_id = request.query_params.get('config')
        if not report_config_id:
            msg = 'Report config wasn\'t passed'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'type': 'ValueError', 'message': msg},
            )

        # check if there is a config of the correct format with the passed config ID
        report_config = get_report_config_by_id(report_config_id)
        if not report_config:
            msg = 'report config is not found or have an incorrect format. JSON is expected'
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': msg},
            )

        # check if the config has all the necessary keys
        try:
            check_report_config(report_config)
        except KeyError as ke:
            return Response(
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                data={'message': ke.args[0]},
            )

        # get the session and the corresponding main package by ID
        result = self.get_object()
        main_pkg = result.root

        ### Get common report data ###

        # form the title according to report config
        title_content = report_config['title_content']
        title = build_report_title(main_pkg, title_content)

        # get run source link
        run_source_link = get_sources(main_pkg)

        # get Bublik run stats link
        run_stats_link = build_absolute_uri(request, f'v2/runs/{main_pkg.id}')

        # get branches
        meta_branches = Meta.objects.filter(
            metaresult__result__id=main_pkg.id,
            type='branch',
        ).values('name', 'value')
        branches = meta_branches

        # get revisions
        meta_revisions = Meta.objects.filter(
            metaresult__result__id=main_pkg.id,
            type='revision',
        ).values('name', 'value')
        revisions = build_revision_references(meta_revisions)

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
                    f'incorrect value for \'axis_y\' key for \'{test_name}\' '
                    'test in the report config'
                )
                return Response(
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    data={'message': msg},
                )

            # filter measurement results by not show params
            not_show_args = test_config['not_show_args']
            mmrs_test = filter_by_not_show_args(mmrs_test, not_show_args)
            if not mmrs_test:
                msg = (
                    f'for \'{test_name}\' test there are no measurement '
                    'results corresponding to the report config',
                )
                logger.warning(msg)
                if test_name in report_config['test_names_order']:
                    report_config['test_names_order'].remove(test_name)

            mmrs_report = mmrs_report.union(mmrs_test)

        mmrs_report = mmrs_report.order_by('id')

        def not_processed_point_conversion(point):
            point = point.__dict__
            point.pop('axis_y')
            point['reasons'] = []
            axis_x_val = list(point['point'].keys())[0] if list(point['point'].keys()) else None
            matches_list = [
                ['point', 'axis_x', axis_x_val],
                [
                    'sequence_group_arg_val',
                    'sequence_group_arg',
                    point['sequence_group_arg_val'],
                ],
            ]
            for match in matches_list:
                if point[match[0]] not in [{}, None]:
                    point['args_vals'][point[match[1]]] = match[2]
                else:
                    point['reasons'].append(
                        f'The test has no argument \'{point[match[1]]}\'',
                    )
                point.pop(match[0])
                point.pop(match[1])
            return point

        # get points with data
        points = []
        not_processed_points = []
        for mmr in mmrs_report:
            point = ReportPoint(mmr, common_args, report_config)
            if not (point.point and point.sequence_group_arg_val is not None):
                not_processed_points.append(not_processed_point_conversion(point))
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

        ### Collect report data ###
        content = [
            {
                'type': 'branch-block',
                'id': 'branches',
                'label': 'Branches',
                'content': branches,
            },
            {
                'type': 'rev-block',
                'id': 'revisions',
                'label': 'Revisions',
                'content': revisions,
            },
        ]
        content += test_records

        report = {
            'title': title,
            'run_source_link': run_source_link,
            'run_stats_link': run_stats_link,
            'version': report_config['version'],
            'content': content,
            'not_processed_points': not_processed_points,
        }

        return Response(data=report, status=status.HTTP_200_OK)
