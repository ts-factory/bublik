# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from datetime import datetime

from django.db.models import Manager, Q, QuerySet
import per_conf

from bublik.data.managers.utils import create_metas_query
from bublik.data.models.meta import Meta


'''
A custom QuerySet allows to do:

>>> test_self = TestIterationResult.objects.filter(test_run=1)
>>> test_results.filter_by_result_classification(properties)

A custom Manager method can return anything you want.
It doesn`t have to return a QuerySet.

[1] https://docs.djangoproject.com/en/dev/topics/db/managers/#custom-managers
[2] https://docs.djangoproject.com/en/dev/topics/db/managers/#calling-custom-queryset-methods-from-the-manager
[3] https://docs.djangoproject.com/en/dev/topics/db/managers/#creating-a-manager-with-queryset-methods
'''


class TestIterationResultQuerySet(QuerySet):
    def filter_by_run_status(self, run_status):
        status_meta_name = getattr(per_conf, 'RUN_STATUS_META', None)
        return self.filter(
            meta_results__meta__name=status_meta_name,
            meta_results__meta__value=run_status,
        )

    def filter_by_run_classification(self, properties):
        compromised_meta_ids = list(
            Meta.objects.filter(name='compromised').values_list('id', flat=True),
        )
        compromised_query = Q(meta_results__meta__in=compromised_meta_ids)

        if ('compromised' in properties) ^ ('notcompromised' in properties):
            if 'compromised' in properties:
                self = self.filter(compromised_query)

            if 'notcompromised' in properties:
                self = self.exclude(compromised_query)

        return self

    def filter_by_result_classification(self, properties):
        if 'expected' in properties and 'unexpected' in properties:
            return self

        err_meta_ids = list(Meta.objects.filter(type='err').values_list('id', flat=True))
        err_query = Q(meta_results__meta__in=err_meta_ids)

        if 'expected' in properties:
            self = self.exclude(err_query)

        elif 'unexpected' in properties:
            self = self.filter(err_query)

        return self

    def filter_by_run_metas(self, metas, meta_types=None):
        if meta_types is None:
            meta_types = ['tag', 'label', 'revision', 'branch']
        if not metas:
            return self

        # Find all meta objects by metas parameter
        query, count = create_metas_query(metas, types=meta_types)
        metas_filter = Meta.objects.filter(query)
        if len(metas_filter) == 0 or count != len(metas_filter):
            # Here a custom exception could be raised
            return self.model.objects.none()

        # Apply filter by run metas
        for meta in metas_filter:
            self = self.filter(meta_results__meta=meta)
        return self

    def filter_runs_by_date(self, from_d: datetime, to_d: datetime) -> QuerySet:
        '''
        This method returns a TestIterationResultsQuerySet which contains
        TestIterationResults which were executed during the specified
        period of time.
        '''

        return self.filter(start__gte=from_d, start__lte=to_d)


class TestIterationResultManager(Manager):
    def get_queryset(self):
        return TestIterationResultQuerySet(self.model, using=self._db)
