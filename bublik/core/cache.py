# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import functools
from typing import ClassVar

from django.core.cache import caches
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.middleware.cache import CacheMiddleware
from django.utils.cache import add_never_cache_headers

from bublik.core.run.tests_organization import get_run_root
from bublik.core.utils import key_value_transforming
from bublik.data.models import Meta, TestIterationResult


class RunCache:
    """
    Caches run data with a key {data_key}.{run_id}.

    It's possible to create RunCache object using TestIterationResult object or
    its ID using: by_obj() or by_id() class methods respectively.
    Until there is no cache validation incomplete run's data aren't cached.

    Usage example:
        cache = RunCache.by_id(run_id, 'stats')
        stats = cache.data
        if not stats:
            cache.data = produce_stats()
    """

    KEY_DATA_CHOICES: ClassVar[set] = {
        'stats',
        'stats_sum',
        'stats_reqs',
        'dashboard-v2',
        'livelog',
        'tree',
    }
    KEYS_EARLY_CACHE: ClassVar[set] = {'livelog'}
    KEYS_TMP_CACHE: ClassVar[set] = {'tree'}

    def __init__(self, run, data_key):
        self.run = run
        self.data_key = data_key
        self.key = self.__generate_cache_key(data_key)
        self._data = self.__get_cache()

    def __generate_cache_key(self, data_key):
        if data_key not in self.KEY_DATA_CHOICES:
            msg = (
                'You try to create cache for unknown data_key, check KEY_DATA_CHOICES of '
                'RunCache class.'
            )
            raise Exception(
                msg,
            )
        return str(self.run.id) + f'.{data_key}'

    def __get_cache(self):
        return caches['run'].get(self.key)

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, data):
        timeout = None
        if self.data_key in self.KEYS_TMP_CACHE:
            timeout = 60 * 20
        if self.run.finish or self.data_key in self.KEYS_EARLY_CACHE:
            self._data = caches['run'].set(self.key, data, timeout)

    @data.deleter
    def data(self):
        caches['run'].delete(self.key)

    @classmethod
    def by_obj(cls, run, data_key):
        if not isinstance(run, TestIterationResult):
            msg = f'Inappropriate type: {type(run)}, expected TestIterationResult'
            raise TypeError(msg)
        return cls(run, data_key)

    @classmethod
    def by_id(cls, run_id, data_key):
        if not (isinstance(run_id, int) or run_id.isdigit()):
            msg = f'Inappropriate type: {type(run_id)}, expected int'
            raise TypeError(msg)
        try:
            run = TestIterationResult.objects.get(id=run_id)
        except ObjectDoesNotExist:
            msg = f"TestIterationResult by id {run_id} doesn't exist, unable to cache its data"
            raise Exception(
                msg,
            ) from ObjectDoesNotExist
        return cls(run, data_key)

    @classmethod
    def delete_data_for_obj(cls, run, data_keys=KEY_DATA_CHOICES):
        for data_key in data_keys:
            self = cls.by_obj(run, data_key)
            del self.data


def set_tags_categories_cache(project_id):
    """
    Tags cache represents the following dict: {'meta_id': 'tag_name=tag_value', }.

    TODO: Can be optimized by expanding cached tags instead of reseting them
    when meta_categorization can be called for a chosen set of metas.
    """

    tags = Meta.objects.filter(type='tag')

    important_tags_data = tags.filter(
        category__priority__range=(1, 3),
        category__project_id=project_id,
    ).order_by('category__priority', 'name')

    relevant_tags_data = tags.filter(
        Q(category__priority__range=(4, 9)) & Q(category__project_id=project_id)
        | Q(category__isnull=True),
    ).order_by('category__priority', 'name')

    tags_data = tags.filter(
        Q(category__priority__range=(1, 9)) & Q(category__project_id=project_id)
        | Q(category__isnull=True),
    ).order_by('category__priority', 'name')

    def prepare_tags(tags_data):
        tags = {}
        for tag in tags_data:
            tags[tag.id] = key_value_transforming(tag.name, tag.value)
        return tags

    caches['run'].set('important_tags', prepare_tags(important_tags_data), None)
    caches['run'].set('relevant_tags', prepare_tags(relevant_tags_data), None)
    caches['run'].set('tags', prepare_tags(tags_data), None)


def cache_page_if_run_done(timeout):
    def _cache_decorator(viewfunc):
        @functools.wraps(viewfunc)
        def _cache_control(request, *args, **kwargs):
            cache = CacheMiddleware(cache_timeout=timeout)

            response = cache.process_request(request)
            if response is not None:
                return response

            response = viewfunc(request, *args, **kwargs)

            # Get run id from kwargs by one of the keys: ('run_id', 'result_id') or return None
            run_id = next(filter(None, [kwargs.get(k) for k in ('run_id', 'result_id')]), None)

            run = None
            if run_id is not None:
                run = get_run_root(run_id)

            if run is None or run.finish:
                cache.process_response(request, response)
            else:
                add_never_cache_headers(response)

            return response

        return _cache_control

    return _cache_decorator
