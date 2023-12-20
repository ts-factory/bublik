# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from django.db.models import Q


def create_metas_query(metas, types):
    count = 0
    query = Q()
    for meta in metas:
        name, *value = meta.split('=', 1)
        query |= Q(name=name, value=value[0] if value else None, type__in=types)
        count += 1
    return query, count
