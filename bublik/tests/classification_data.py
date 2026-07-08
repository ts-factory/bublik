# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Minimal data for the tests of result classification.

A project gets a test tree ``suite/net/{ping,reset}`` (a session, a package
and two tests), and a run of it gets results of those tests, each with its
obtained result, verdicts and, when unexpected, an ``err`` meta.
"""

from __future__ import annotations

from datetime import timedelta
import itertools
from unittest import mock

from cachalot.api import cachalot_disabled
from django.conf import settings
from django.core.cache import caches
from django.test import override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from bublik.core.config.services import ConfigServices
from bublik.data import models


# Run stats, configs and metas are cached in memory of their own, emptied
# before every test, so that no cached data comes from another test or
# another database.
LOCMEM_CACHES = {
    alias: (
        config
        if alias == 'default'
        else {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': f'classification-tests-{alias}',
        }
    )
    for alias, config in settings.CACHES.items()
}


def isolated_caches(test_class):
    """
    Class decorator: the caches above, and no query caching, as a test makes
    several requests in one transaction, which a query cache treats as one.
    """
    test_class = override_settings(CACHES=LOCMEM_CACHES)(test_class)
    set_up = test_class.setUp

    def setUp(self):
        no_query_cache = cachalot_disabled()
        no_query_cache.__enter__()
        self.addCleanup(no_query_cache.__exit__, None, None, None)
        for alias in LOCMEM_CACHES:
            if alias != 'default':
                caches[alias].clear()
        set_up(self)

    test_class.setUp = setUp
    return test_class


_serial = itertools.count(1)


def meta(meta_type, value, name=None):
    obj, _ = models.Meta.objects.get_or_create(
        type=meta_type,
        name=name,
        value=value,
        defaults={'hash': f'{meta_type}:{name}:{value}'},
    )
    return obj


def argument(name, value):
    obj, _ = models.TestArgument.objects.get_or_create(
        name=name,
        value=value,
        defaults={'hash': f'{name}={value}'},
    )
    return obj


def iteration(test, **parameters):
    obj = models.TestIteration.objects.create(test=test, hash=f'iteration-{next(_serial)}')
    obj.test_arguments.set([argument(name, value) for name, value in parameters.items()])
    return obj


class TestTree:
    """The test tree suite/net/{ping,reset}, with an iteration per node."""

    def __init__(self):
        self.suite = models.Test.objects.create(
            name='suite',
            result_type=models.ResultType.conv('session'),
        )
        self.net = models.Test.objects.create(
            name='net',
            parent=self.suite,
            result_type=models.ResultType.conv('pkg'),
        )
        self.ping = models.Test.objects.create(
            name='ping',
            parent=self.net,
            result_type=models.ResultType.conv('test'),
        )
        self.reset = models.Test.objects.create(
            name='reset',
            parent=self.net,
            result_type=models.ResultType.conv('test'),
        )
        self.suite_iteration = iteration(self.suite)
        self.net_iteration = iteration(self.net)


def run(project, tags=()):
    """A run of a project, with the given (name, value) tags."""
    start = timezone.now() - timedelta(hours=1)
    obj = models.TestIterationResult.objects.create(start=start, finish=start, project=project)
    for name, value in tags:
        models.MetaResult.objects.create(result=obj, meta=meta('tag', value, name))
    return obj


def result(run_row, test_iteration, status='FAILED', verdicts=(), unexpected=True):
    """A result of an iteration in a run, unexpected (with `err`) by default."""
    start = run_row.start + timedelta(seconds=next(_serial))
    obj = models.TestIterationResult.objects.create(
        iteration=test_iteration,
        test_run=run_row,
        project=run_row.project,
        start=start,
        finish=start,
        exec_seqno=next(_serial),
    )
    models.MetaResult.objects.create(result=obj, meta=meta('result', status))
    for serial, verdict in enumerate(verdicts):
        models.MetaResult.objects.create(
            result=obj,
            meta=meta('verdict', verdict),
            serial=serial,
        )
    if unexpected:
        models.MetaResult.objects.create(result=obj, meta=meta('err', 'unexpected'))
    return obj


def issue(project, title='issue', **fields):
    return models.Issue.objects.create(project=project, title=title, **fields)


def rule(issue_row, test, category=models.IssueCategory.KNOWN_ISSUE, **fields):
    fields.setdefault('expected', models.default_expected_for(category))
    return models.IssueRule.objects.create(
        issue=issue_row,
        test=test,
        category=category,
        **fields,
    )


def stamp(result_row, rule_row, origin=models.RuleResultOrigin.IMPORT):
    return models.RuleResult.objects.create(
        result=result_row,
        issue_rule=rule_row,
        origin=origin,
    )


def admin(email='admin@example.com'):
    return models.User.objects.create(
        email=email,
        roles=models.UserRoles.ADMIN,
        status=models.UserStatus.ACTIVE,
    )


def login(client, user):
    client.cookies['access_token'] = str(AccessToken.for_user(user))


def allow_without_login(*project_ids):
    """Let the given projects, and only them, manage issues without a login."""
    getattr_from_global = ConfigServices.getattr_from_global

    def fake(config_name, data_key, project_id=None, **kwargs):
        if data_key != 'NOT_PERMISSION_REQUIRED_ACTIONS':
            return getattr_from_global(config_name, data_key, project_id, **kwargs)
        allowed = project_id is not None and int(project_id) in project_ids
        return ['manage_issues'] if allowed else []

    return mock.patch.object(ConfigServices, 'getattr_from_global', side_effect=fake)
