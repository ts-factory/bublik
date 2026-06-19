# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

'''
Single source of truth for classification "suppression": a failed result is
effectively expected (and must not count as unexpected) when it has a
ResultClassification stamp whose rule is `expected` and whose issue is `open`.
'''

from django.db.models import OuterRef

from bublik.data.models.classification import ResultClassification


# Predicate on a ResultClassification row.
SUPPRESSION_FILTER = {'rule__expected': True, 'rule__issue__state': 'open'}

# Same predicate across TestIterationResult.classifications, for
# QuerySet.exclude(**SUPPRESSED_RELATION_FILTER) / .filter(...).
SUPPRESSED_RELATION_FILTER = {
    f'classifications__{key}': value for key, value in SUPPRESSION_FILTER.items()
}


def suppressed_subquery(outer_field='id'):
    '''ResultClassification rows that suppress the outer result's
    unexpectedness. Use inside Exists(): `Exists(suppressed_subquery())`.'''
    return ResultClassification.objects.filter(
        result_id=OuterRef(outer_field),
        **SUPPRESSION_FILTER,
    )
