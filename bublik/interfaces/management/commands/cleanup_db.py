# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from django.contrib.postgres.aggregates import ArrayAgg
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, Exists, OuterRef

from bublik.data.models import (
    ChartView,
    MeasurementResult,
    MeasurementResultList,
    View,
)


class Command(BaseCommand):
    def move_mmr_sequences(self):
        '''
        Find sequences among MeasurementResult objects and move them
        to the MeasurementResultList.
        '''
        self.stdout.write('Move measurement result sequences:')

        # get mmr values sequences
        aggregated_data = (
            MeasurementResult.objects.values('result', 'measurement')
            .annotate(value_list=ArrayAgg('value', ordering='serial'), count=Count('id'))
            .filter(count__gt=1)
        )

        # save the sequences as MeasurementResultList objects and
        # delete the corresponding MeasurementResult objects
        created_mmrl_count = 0
        all_deleted_mmr_count = 0
        with transaction.atomic():
            for data in aggregated_data:
                MeasurementResultList.objects.create(
                    result_id=data['result'],
                    measurement_id=data['measurement'],
                    value=data['value_list'],
                )
                created_mmrl_count += 1

                deleted_mr_count, _ = MeasurementResult.objects.filter(
                    result_id=data['result'],
                    measurement_id=data['measurement'],
                ).delete()
                all_deleted_mmr_count += deleted_mr_count

        self.stdout.write(
            self.style.SUCCESS(
                f'\tCREATED: {created_mmrl_count} MeasurementResultList objects',
            ),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {all_deleted_mmr_count} MeasurementResult objects',
            ),
        )

    def delete_incorrect_cv(self):
        '''
        Delete type X and Y ChartView objects linking unit measurements to line-graph
        type views.
        '''
        self.stdout.write('Delete incorrect ChartView objects:')

        deleted_cv_count, _ = (
            ChartView.objects.exclude(type='P')
            .filter(
                Exists(
                    MeasurementResult.objects.filter(
                        measurement=OuterRef('measurement'),
                        result=OuterRef('result'),
                    ),
                ),
            )
            .delete()
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {deleted_cv_count=} ChartView objects',
            ),
        )

    def delete_duplicate_cv(self):
        '''
        Delete duplicate ChartView objects.
        '''
        self.stdout.write('Delete duplicate ChartView objects:')

        duplicate = 0
        with transaction.atomic():
            unique_views_data = set()
            for cv in ChartView.objects.all():
                view_data = (cv.type, cv.measurement, cv.result, cv.view)
                if view_data in unique_views_data:
                    cv.delete()
                    duplicate += 1
                else:
                    unique_views_data.add(view_data)

        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {duplicate} ChartView objects',
            ),
        )

    def delete_unused_views(self):
        '''
        Delete View objects that are not referenced by any ChartView object.
        '''
        self.stdout.write('Delete unused View objects:')

        unused, _ = View.objects.exclude(
            id__in=ChartView.objects.values('view_id'),
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {unused} View objects',
            ),
        )

    def handle(self, *args, **options):
        self.move_mmr_sequences()
        self.delete_incorrect_cv()
        self.delete_duplicate_cv()
        self.delete_unused_views()
