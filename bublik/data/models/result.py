# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from itertools import chain
from typing import ClassVar

from django.db import models
from django.utils.functional import cached_property

from bublik.core.config.services import ConfigServices
from bublik.core.queries import MetaResultsQuery, get_or_none
from bublik.core.utils import get_difference
from bublik.data.managers import TestIterationResultManager
from bublik.data.models import GlobalConfigs
from bublik.data.models.meta import Meta
from bublik.data.models.reference import Reference


__all__ = [
    'MetaResult',
    'MetaTest',
    'ResultStatus',
    'ResultType',
    'RunConclusion',
    'RunStatus',
    'RunStatusByUnexpected',
    'Test',
    'TestArgument',
    'TestIteration',
    'TestIterationRelation',
    'TestIterationResult',
]


class RunStatus:
    '''
    This is an interface to describe all run statuses.
    '''

    DONE = 'DONE'
    RUNNING = 'RUNNING'
    WARNING = 'WARNING'
    ERROR = 'ERROR'
    STOPPED = 'STOPPED'
    BUSY = 'BUSY'

    @classmethod
    def all(cls):
        return [value for name, value in vars(cls).items() if name.isupper()]


class RunStatusByUnexpected:
    '''
    This is an interface to describe all run statuses depending on unexpected rate.
    '''

    SUCCESS = 'success'
    WARNING = 'warning'
    ERROR = 'error'

    @classmethod
    def all(cls):
        return [value for name, value in vars(cls).items() if name.isupper()]

    @classmethod
    def identify(cls, run_stats):
        total = run_stats['total']
        unexpected = run_stats['unexpected']
        unexpected_percent = round(unexpected / total * 100) if total else 0
        left_border, right_border = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'RUN_STATUS_BY_NOK_BORDERS',
            default=(20.0, 80.0),
        )

        if total == 0 or unexpected_percent >= right_border:
            return cls.ERROR, unexpected_percent
        if unexpected_percent > left_border and unexpected_percent < right_border:
            return cls.WARNING, unexpected_percent
        return cls.SUCCESS, unexpected_percent


class RunConclusion:
    '''
    This is an interface to describe all run conclusions.
    '''

    OK = 'run-ok'
    RUNNING = 'run-running'
    WARNING = 'run-warning'
    ERROR = 'run-error'
    STOPPED = 'run-stopped'
    BUSY = 'run-busy'
    COMPROMISED = 'run-compromised'

    @classmethod
    def all(cls):
        return [value for name, value in vars(cls).items() if name.isupper()]

    @classmethod
    def identify(
        cls,
        run_status,
        run_status_by_nok,
        unexpected_percent,
        run_compromised,
        driver_unload,
    ):
        # conclusion by compromised meta
        if run_compromised:
            reason = 'Run marked as compromised'
            return cls.COMPROMISED, reason

        # conclusion by run status
        conclusions_by_statuses = {
            RunStatus.RUNNING: cls.RUNNING,
            RunStatus.WARNING: cls.WARNING,
            RunStatus.ERROR: cls.ERROR,
            RunStatus.STOPPED: cls.STOPPED,
            RunStatus.BUSY: cls.BUSY,
        }
        run_status_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'RUN_STATUS_META',
        )
        if run_status in conclusions_by_statuses:
            reason = f'{run_status_meta} reported by TE is {run_status}'
            return conclusions_by_statuses[run_status], reason

        # conclusion by nok
        if run_status == RunStatus.DONE and run_status_by_nok == RunStatusByUnexpected.WARNING:
            reason = f'{unexpected_percent}% of the results are unexpected'
            return cls.WARNING, reason

        if run_status_by_nok == RunStatusByUnexpected.ERROR:
            reason = f'{unexpected_percent}% of the results are unexpected'
            return cls.ERROR, reason

        # conclusion by unknown DU or run_status
        if driver_unload not in ['OK', 'REUSE', '-', None]:
            reason = f'Unknown DU reported by TE: {driver_unload}'
            return cls.ERROR, reason

        if run_status not in RunStatus.all():
            reason = f'Unknown {run_status_meta} reported by TE: {run_status}'
            return cls.ERROR, reason

        return cls.OK, None


class ResultStatus:
    '''
    This is an interface gathering different result statuses to the fixed group.
    Such a system is useful to have one interface for all projects allowing them
    have any result statuses they want just assigning them to the fixed group.
    '''

    RESULT_STATUSES_BY_GROUPS: ClassVar[dict] = {
        'passed': ['PASSED'],
        'failed': ['FAILED'],
        'skipped': ['SKIPPED'],
        'abnormal': ['KILLED', 'CORED', 'FAKED', 'INCOMPLETE'],
    }

    @staticmethod
    def all_statuses():
        available_statuses = ResultStatus.RESULT_STATUSES_BY_GROUPS.values()
        return list(chain.from_iterable(available_statuses))

    @staticmethod
    def discover_groups(statuses):
        diff = get_difference(statuses, ResultStatus.all_statuses())
        if diff:
            msg = f'Unknown result statuses: {diff}'
            raise RuntimeError(msg)

        groups = set()
        for group, statuses_of_group in ResultStatus.RESULT_STATUSES_BY_GROUPS.items():
            if any(status in statuses_of_group for status in statuses):
                groups.add(group)

        return groups

    @staticmethod
    def discover_statuses(groups):
        available_groups = ResultStatus.RESULT_STATUSES_BY_GROUPS.keys()
        diff = get_difference(groups, available_groups)
        if diff:
            msg = f'Unknown result groups: {diff}'
            raise RuntimeError(msg)

        statuses = set()
        for group in groups:
            statuses = statuses.union(ResultStatus.RESULT_STATUSES_BY_GROUPS[group])

        return statuses


class ResultType:
    '''
    This class stands for converting a type of result to the one symbol.
    '''

    TEST = 'test'
    SESSION = 'session'
    PACKAGE = 'pkg'

    SET: ClassVar[dict] = {TEST: 'T', SESSION: 'S', PACKAGE: 'P'}

    INV_SET: ClassVar[dict] = {v: k for k, v in SET.items()}

    @classmethod
    def conv(cls, item):
        return cls.SET.get(item)

    @classmethod
    def inv(cls, item):
        return cls.INV_SET.get(item)

    @classmethod
    def default(cls):
        return cls.SET.get(cls.TEST)

    @classmethod
    def choices(cls):
        return tuple(cls.INV_SET.items())


class Test(models.Model):
    '''
    The table contains test names and relations to other tests.
    '''

    name = models.CharField(max_length=64, help_text='The test name')

    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        help_text='''\
    A parent test (package) identifier - can be used to reassemble the test path.
    Note! This path does not include the test and package arguments, it represents
    only packages path like 'package1/package2/package3/test'.''',
    )

    result_type = models.CharField(
        max_length=1,
        choices=ResultType.choices(),
        default=ResultType.default(),
        help_text='It distinguishes packages, sessions and tests from each other',
    )

    class Meta:
        db_table = 'bublik_test'
        unique_together = (('name'), ('parent'))

    class Admin:
        pass

    def get_descendants(self):
        return Test.objects.filter(parent=self).order_by('name')

    def __repr__(self):
        return f'Test(name={self.name!r}, parent={self.parent!r})'


class TestArgument(models.Model):
    '''
    Test parameters with possible values.
    '''

    hashable = ('name', 'value')

    name = models.TextField(help_text='The argument name.')
    value = models.TextField(blank=True, help_text='The argument value.')
    hash = models.CharField(max_length=64, unique=True, help_text='Name and value hash.')

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_testargument'

    def __repr__(self):
        return f'TestArgument(name={self.name!r}, value={self.value!r}, hash={self.hash!r})'


class TestIteration(models.Model):
    '''
    A test with a determined set of argument values.
    '''

    test = models.ForeignKey(Test, on_delete=models.CASCADE, help_text='The test identifier.')

    test_arguments = models.ManyToManyField(
        TestArgument,
        related_name='test_iterations',
        help_text='The test arguments.',
    )
    hash = models.CharField(max_length=64, null=True, help_text='Hash')

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_testiteration'

    def __repr__(self):
        return (
            f'TestIteration(test={self.test!r}, test_arguments={self.test_arguments!r}, '
            f'hash={self.hash!r})'
        )

    def get_packages(self, package_strip=None, package_ignore=None):
        '''
        Get list of all packages from the database ignoring packages listed in
        package_ignore. Packages are returned in string representation
        including path, substring package_strip is dropped form a package
        pathname.
        '''
        packages = []
        iterations = TestIteration.objects.filter(hash__isnull=True)
        for iteration in iterations:
            pname = iteration.test.name
            relatives = TestIterationRelation.objects.filter(
                test_iteration=iteration.id,
            ).order_by('depth')

            for relation in relatives:
                if not relation.parent_iteration:
                    break
                pname = relation.parent_iteration.test.name + '/' + pname

            if package_strip:
                pname = pname.replace(package_strip, '')

            ignore = False
            if package_ignore:
                for pattern in package_ignore:
                    if pattern in pname:
                        ignore = True
                        break

            if not ignore:
                packages.append(pname)

        packages.sort()
        return packages


class TestIterationRelation(models.Model):
    '''
    The table serves to connect all related test iterations. A test package
    can have test arguments and it is considered to be a test iteration. The
    table is excessive and keeps all parents for each test iteration. In the
    result all parents of a test iteration can be extracted by one request
    to the database.
    '''

    test_iteration = models.ForeignKey(
        TestIteration,
        on_delete=models.CASCADE,
        related_name='parent_relations',
        help_text='The test iteration identifier.',
    )

    parent_iteration = models.ForeignKey(
        TestIteration,
        on_delete=models.CASCADE,
        related_name='child_relations',
        null=True,
        help_text='A parent iteration identifier.',
    )
    depth = models.IntegerField(help_text='The parent depth.')

    class Meta:
        db_table = 'bublik_testiterationrelation'
        unique_together = (('test_iteration'), ('depth'))

    class Admin:
        pass

    def __repr__(self):
        return (
            f'TestIterationRelation(test_iteration={self.test_iteration!r}, '
            f'parent_iteration={self.parent_iteration!r}, depth={self.depth!r}'
        )


class TestIterationResult(models.Model):
    '''
    Result of a test iteration execution. Note! a test run (sequence of test
    iteration results) is considered to be another one TestIterationResult
    object. All test iteration results which belong to a test run refer to
    this test run using field test_run.
    '''

    iteration = models.ForeignKey(
        TestIteration,
        on_delete=models.CASCADE,
        null=True,
        help_text='The test iteration identifier or none.',
    )

    test_run = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        help_text='Reference to the test run.',
    )

    parent_package = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        related_name='results',
        help_text='Parent package to which the iteration belongs',
    )

    tin = models.IntegerField(
        null=True,
        help_text='''\
The test iteration identifier (TIN) which is generated during the testing.''',
    )

    exec_seqno = models.IntegerField(
        null=True,
        help_text='''\
The execution sequence number (the actual test ID) which is generated during the testing.''',
    )

    start = models.DateTimeField(
        db_index=True,
        help_text='''\
Timestamp of the iteration (or test run) execution start.''',
    )

    finish = models.DateTimeField(
        null=True,
        help_text='''\
Timestamp of the iteration (or test run) execution end.''',
    )

    objects = TestIterationResultManager()

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_testiterationresult'

    def __repr__(self):
        return (
            f'TestIterationResult(id={self.id!r}, iteration={self.iteration!r}, '
            f'test_run={self.test_run!r}, tin={self.tin!r}, exec_seqno={self.exec_seqno!r}, '
            f'start={self.start!r}, finish={self.finish!r})'
        )

    @cached_property
    def root(self):
        '''
        Get root test iteration result from any test iteration result.
        Necessary while test_run of root is None: bug 11428.
        '''
        if not self.test_run:
            return self
        return self.test_run

    @cached_property
    def main_package(self):
        '''
        Get main package of the run from any test iteration result.
        For root test iteration result and main package itself the actual
        main package is returned.
        '''
        return (
            TestIterationResult.objects.filter(test_run=self.root, parent_package__isnull=True)
            .order_by('start')
            .first()
        )

    @property
    def duration(self):
        """
        Get test iteration result duration or None if it's not finished yet.
        """
        return self.finish - self.start if self.finish else None

    @cached_property
    def import_mode(self):
        '''
        Get import mode of the run from any test iteration result.
        '''
        meta_result = get_or_none(
            MetaResult.objects,
            result=self.root,
            meta__name='import_mode',
            meta__type='import',
        )

        if not meta_result:
            return None
        return meta_result.meta.value

    def run_metas(self):
        return MetaResult.objects.filter(result=self.root).select_related('meta')

    def branches(self):
        return MetaResultsQuery(self.run_metas()).metas_query('branch')


class MetaResult(models.Model):
    '''
    The table connects a test iteration results with metadata and references.
    '''

    meta = models.ForeignKey(Meta, on_delete=models.CASCADE, help_text='A metadata identifier.')
    reference = models.ForeignKey(
        Reference,
        on_delete=models.CASCADE,
        null=True,
        help_text='A reference identifier.',
    )
    ref_index = models.IntegerField(
        null=True,
        help_text='''\
A reference index, can be used for example to specify a line in the log.''',
    )
    serial = models.IntegerField(
        default=0,
        help_text='''\
Serial number of a meta result, can be used to determine verdicts order.''',
    )
    result = models.ForeignKey(
        TestIterationResult,
        on_delete=models.CASCADE,
        related_name='meta_results',
        help_text='The test iteration result identifier.',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_metaresult'

    def __repr__(self):
        return (
            f'MetaResult(meta={self.meta!r}, reference={self.reference!r}, '
            f'ref_index={self.ref_index!r}, serial={self.serial!r}, result={self.result!r})'
        )


class MetaTest(models.Model):
    '''
    The table connects a test with metadata.
    '''

    updated = models.DateTimeField(help_text='Timestamp of the connection creation.')
    meta = models.ForeignKey(
        Meta,
        on_delete=models.CASCADE,
        help_text='A metadata identifier.',
    )
    serial = models.IntegerField(
        default=0,
        help_text='The serial number is used to determine the order of comments.',
    )
    test = models.ForeignKey(
        Test,
        on_delete=models.CASCADE,
        help_text='The test identifier.',
    )

    class Admin:
        pass

    class Meta:
        db_table = 'bublik_metatest'

    def delete(self, *args, **kwargs):
        meta_id = self.meta_id
        super().delete(*args, **kwargs)
        metatests_with_meta_id = MetaTest.objects.filter(meta_id=meta_id)
        if not metatests_with_meta_id:
            Meta.objects.get(id=meta_id).delete()

    def __repr__(self):
        return f'MetaTest(test={self.test!r}, serial={self.serial!r}, meta={self.meta!r})'
