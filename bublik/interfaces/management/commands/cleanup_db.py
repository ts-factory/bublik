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

        with transaction.atomic():
            # save the sequences as MeasurementResultList objects
            mmr_lists = [
                MeasurementResultList(
                    result_id=ad['result'],
                    measurement_id=ad['measurement'],
                    value=ad['value_list'],
                )
                for ad in aggregated_data
            ]
            created_mmrl = MeasurementResultList.objects.bulk_create(mmr_lists)

            # delete the corresponding MeasurementResult objects
            deleted_mmr_count, _ = MeasurementResult.objects.filter(
                Exists(
                    MeasurementResultList.objects.filter(
                        result=OuterRef('result'),
                        measurement=OuterRef('measurement'),
                    ),
                ),
            ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'\tCREATED: {len(created_mmrl)} MeasurementResultList objects',
            ),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f'\tDELETED: {deleted_mmr_count} MeasurementResult objects',
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
                f'\tDELETED: {deleted_cv_count} ChartView objects',
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
        with transaction.atomic():
            all_deleted_cv_count = 0
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
        self.move_mmr_sequences()
        self.delete_incorrect_cv()
        self.delete_duplicate_cv()
        self.delete_unused_views()
