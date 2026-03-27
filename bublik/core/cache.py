# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

import functools
from typing import ClassVar

from django.core.cache import caches
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.middleware.cache import CacheMiddleware
from django.utils.cache import add_never_cache_headers

from bublik.core.run.tests_organization import get_run_root
from bublik.core.utils import key_value_transforming
from bublik.data import models


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
        if not isinstance(run, models.TestIterationResult):
            msg = f'Inappropriate type: {type(run)}, expected TestIterationResult'
            raise TypeError(msg)
        return cls(run, data_key)

    @classmethod
    def by_id(cls, run_id, data_key):
        if not (isinstance(run_id, int) or run_id.isdigit()):
            msg = f'Inappropriate type: {type(run_id)}, expected int'
            raise TypeError(msg)
        try:
            run = models.TestIterationResult.objects.get(id=run_id)
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


class ProjectCache:
    CACHE_ALIAS = 'project'

    def __init__(self, project_id: int):
        self._project_id = project_id
        self._content = caches[self.CACHE_ALIAS]

    @property
    def configs(self):
        return _ConfigsCache(self)

    @property
    def tags(self):
        return _TagsCache(self)

    @property
    def tests(self):
        return _TestsCache(self)


class _ProjectSectionCache:
    '''
    Base class for project section caches.

    Each section cache stores data under keys like
    'project:{project_id}:{SECTION}:{data_key}'.
    '''

    SECTION: ClassVar[str] | None = None
    KEY_DATA_CHOICES: ClassVar[set[str]] | None = None

    def __init__(self, project_cache: ProjectCache):
        if self.SECTION is None:
            msg = f'{self.__class__.__name__} must define SECTION'
            raise RuntimeError(msg)
        if self.KEY_DATA_CHOICES is None:
            msg = f'{self.__class__.__name__} must define KEY_DATA_CHOICES'
            raise RuntimeError(msg)
        self._project = project_cache

    def _cache_key(self, data_key: str) -> str:
        return f'project:{self._project._project_id}:{self.SECTION}:{data_key}'

    def _validate(self, data_key: str) -> None:
        if data_key not in self.KEY_DATA_CHOICES:
            msg = (
                f'Unknown {self.SECTION} data key: {data_key}. '
                f'Possible: {self.KEY_DATA_CHOICES}.'
            )
            raise KeyError(msg)

    def get(self, data_key: str):
        self._validate(data_key)
        return self._project._content.get(self._cache_key(data_key))

    def set(self, data_key: str, value, timeout: int | None = None):
        self._validate(data_key)
        self._project._content.set(self._cache_key(data_key), value, timeout)

    def delete(self, data_key: str):
        self._validate(data_key)
        self._project._content.delete(self._cache_key(data_key))

    def clear_all(self):
        for data_key in self.KEY_DATA_CHOICES:
            self.delete(data_key)


class _ConfigsCache(_ProjectSectionCache):
    SECTION = 'configs'
    KEY_DATA_CHOICES: ClassVar[set[str]] = set(models.GlobalConfigs.all())


class _TagsCache(_ProjectSectionCache):
    SECTION = 'tags'
    KEY_DATA_CHOICES: ClassVar[set] = {
        'important',
        'relevant',
        'all',
    }

    def load(self):
        '''
        Populate tags cache for the project.

        Each cache entry stores a mapping of meta tag IDs to their string
        representation produced by ``key_value_transforming``.

        Cache keys:
            - 'important': project-related tags with category priority 1-3
            - 'relevant': project-related tags with category priority 4-9
              or without a category
            - 'all': all project-related tags with category priority 1-9
              or without a category

        Value format:
            {meta_id: 'tag_name=tag_value'}
        '''

        tags = models.Meta.objects.filter(type='tag')

        important_tags_data = tags.filter(
            category__priority__range=(1, 3),
            category__project_id=self._project._project_id,
        ).order_by('category__priority', 'name')

        relevant_tags_data = tags.filter(
            Q(category__priority__range=(4, 9))
            & Q(category__project_id=self._project._project_id)
            | Q(category__isnull=True),
        ).order_by('category__priority', 'name')

        tags_data = tags.filter(
            Q(category__priority__range=(1, 9))
            & Q(category__project_id=self._project._project_id)
            | Q(category__isnull=True),
        ).order_by('category__priority', 'name')

        def prepare_tags(tags_data):
            tags = {}
            for tag in tags_data:
                tags[tag.id] = key_value_transforming(tag.name, tag.value)
            return tags

        self.set('important', prepare_tags(important_tags_data))
        self.set('relevant', prepare_tags(relevant_tags_data))
        self.set('all', prepare_tags(tags_data))


class _TestsCache(_ProjectSectionCache):
    SECTION = 'tests'
    KEY_DATA_CHOICES: ClassVar[set] = {
        'test_names_and_paths',
    }

    def load(self):
        all_nodes = models.Test.objects.only('id', 'name', 'parent_id', 'result_type')
        all_nodes_by_id = {n.id: n for n in all_nodes}

        test_entity = models.ResultType.conv('test')

        if self._project._project_id is not None:
            project_node_ids = set(
                models.TestIterationResult.objects.filter(project_id=self._project._project_id)
                .values_list('iteration__test_id', flat=True)
                .distinct(),
            )
            tests = sorted(
                (
                    node
                    for node in all_nodes_by_id.values()
                    if node.result_type == test_entity and node.id in project_node_ids
                ),
                key=lambda n: n.name,
            )
        else:
            tests = sorted(
                (node for node in all_nodes_by_id.values() if node.result_type == test_entity),
                key=lambda n: n.name,
            )

        seen = set()
        test_search_options = []

        for test in tests:
            path = []
            node = test
            while node:
                path.append(node.name)
                node = all_nodes_by_id.get(node.parent_id)
            path_str = '/'.join(reversed(path))

            if test.name not in seen:
                seen.add(test.name)
                test_search_options.append(test.name)

            if path_str not in seen:
                seen.add(path_str)
                test_search_options.append(path_str)

        self.set('test_names_and_paths', test_search_options)
