# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 OKTET Labs Ltd. All rights reserved.

from itertools import islice

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
    def delete_duplicate_mmr(self):
        '''
        Delete duplicate MeasurementResult objects.
        '''
        self.stdout.write('Delete duplicate MeasurementResult objects:')
        mmr_dups_iterator = (
            MeasurementResult.objects.values('result', 'serial', 'measurement', 'value')
            .annotate(count=Count('id'))
            .filter(count__gt=1)
            .iterator()
        )

        all_deleted_mmr_num = 0
        with transaction.atomic():
            for mmr_dup in mmr_dups_iterator:
                mmr = MeasurementResult.objects.filter(
                    result=mmr_dup['result'],
                    serial=mmr_dup['serial'],
                    measurement=mmr_dup['measurement'],
                    value=mmr_dup['value'],
                )
                deleted_mmr_num, _ = mmr.exclude(id=mmr.first().id).delete()
                all_deleted_mmr_num += deleted_mmr_num
        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {all_deleted_mmr_num} MeasurementResult objects',
            ),
        )

    def move_mmr_sequences(self):
        '''
        Find sequences among MeasurementResult objects and move them
        to the MeasurementResultList.
        '''
        self.stdout.write('Move measurement result sequences:')

        mmrl_start_num = MeasurementResultList.objects.count()

        # get all measurements with results
        unique_measurement_iterator = (
            MeasurementResult.objects.values_list('measurement', flat=True)
            .distinct()
            .iterator()
        )

        all_deleted_mmr_num = 0
        batch_size = 5000

        for measurement in unique_measurement_iterator:
            results = (
                MeasurementResult.objects.filter(measurement=measurement)
                .values_list('result', flat=True)
                .distinct()
                .iterator()
            )
            while True:
                with transaction.atomic():
                    result_batch = list(islice(results, batch_size))
                    if not result_batch:
                        break
                    aggregated_data = (
                        MeasurementResult.objects.filter(
                            measurement=measurement,
                            result__in=result_batch,
                        )
                        .values('result')
                        .annotate(
                            value_list=ArrayAgg('value', ordering=['serial', 'id']),
                            count=Count('id'),
                        )
                        .filter(count__gt=1)
                        .iterator()
                    )

                    mmr_lists = []
                    results_for_del = set()
                    for ad in aggregated_data:
                        mmr_lists.append(
                            MeasurementResultList(
                                result_id=ad['result'],
                                measurement_id=measurement,
                                value=ad['value_list'],
                            ),
                        )
                        results_for_del.add(ad['result'])

                    MeasurementResultList.objects.bulk_create(mmr_lists)

                    deleted_mmr_num, _ = MeasurementResult.objects.filter(
                        measurement_id=measurement,
                        result__id__in=results_for_del,
                    ).delete()
                    all_deleted_mmr_num += deleted_mmr_num

        created_mmrl_num = MeasurementResultList.objects.count() - mmrl_start_num
        self.stdout.write(
            self.style.SUCCESS(
                f'\tCREATED: {created_mmrl_num} MeasurementResultList objects',
            ),
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {all_deleted_mmr_num} MeasurementResult objects',
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
                f'\tDELETED: {deleted_cv_count} ChartView objects (incorrect type)',
            ),
        )

        deleted_cv_count, _ = ChartView.objects.filter(measurement__isnull=True).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {deleted_cv_count} ChartView objects (empty measurement)',
            ),
        )

    def delete_duplicate_cv(self):
        '''
        Delete duplicate ChartView objects.
        '''
        self.stdout.write('Delete duplicate ChartView objects:')

        cv_dups = (
            ChartView.objects.values('type', 'measurement', 'result', 'view')
            .annotate(count=Count('id'))
            .filter(count__gt=1)
        )
        all_deleted_cv_count = 0
        with transaction.atomic():
            for cv_dup in cv_dups:
                cv = ChartView.objects.filter(
                    type=cv_dup['type'],
                    measurement=cv_dup['measurement'],
                    result=cv_dup['result'],
                    view=cv_dup['view'],
                )
                deleted_cv_count, _ = cv.exclude(id=cv.first().id).delete()
                all_deleted_cv_count += deleted_cv_count
        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {all_deleted_cv_count} ChartView objects',
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
        self.delete_duplicate_mmr()
        self.move_mmr_sequences()
        self.delete_incorrect_cv()
        self.delete_duplicate_cv()
        self.delete_unused_views()
