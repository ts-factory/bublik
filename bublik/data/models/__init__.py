# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
"""
The database is destined to keep mass automatic testing results.
In general it is designed to be flexible and keep tests structured in various
ways. Tests results can have links to different kinds of external or
internal resources, arbitrary sets of generated and human-added metadata.

The database is tested using the following tests structure (see terminology.yml):
  - Test run:
    - package1 (args = arg1, arg2 ... argn)
      - package2 (args = arg3, arg4 ... argn)
        - package3 (args = arg5, arg6 ... argn)
          - iteration (args = arg7, arg8 ... argn)
    ...
    - packageX
A test result path (relatively to the test run):
  package1:args/package2:args/package3:args/iteration:args

The database is capable to keep thousands of test runs which contain thousands
of test iteration results.
"""

# isort would hoist `.classification` above `.eventlog`, but classification.py
# triggers the core.config->cache->utils->EventLog chain at import time, so it
# must be imported after EventLog to avoid a partial-init circular import.
from .config import Config, ConfigTypes, GlobalConfigs  # noqa: I001
from .endpoint_url import EndpointURL
from .eventlog import EventLog
from .expectation import Expectation, ExpectMeta
from .job import (
    ImportJob,
    Job,
    JobTaskExecution,
    JobTaskExecutionResult,
    TaskExecution,
)
from .measurement import (
    ChartView,
    ChartViewType,
    Measurement,
    MeasurementResult,
    MeasurementResultList,
    View,
)
from .meta import (
    Meta,
    MetaCategory,
    MetaPattern,
)
from .project import Project
from .reference import Reference
from .result import (
    MetaResult,
    MetaTest,
    ResultStatus,
    ResultType,
    RunConclusion,
    RunStatus,
    RunStatusByUnexpected,
    Test,
    TestArgument,
    TestIteration,
    TestIterationRelation,
    TestIterationResult,
)
from .classification import (
    Issue,
    IssueCategory,
    IssueExt,
    IssueRule,
    IssueState,
    ResultClassification,
    StampOrigin,
    default_expected_for,
)
from .user import User, UserManager, UserRoles


__all__ = [
    'ChartView',
    'ChartViewType',
    'Config',
    'ConfigTypes',
    'EndpointURL',
    'EventLog',
    'ExpectMeta',
    'Expectation',
    'GlobalConfigs',
    'ImportJob',
    'Issue',
    'IssueCategory',
    'IssueExt',
    'IssueRule',
    'IssueState',
    'Job',
    'JobTaskExecution',
    'JobTaskExecutionResult',
    'Measurement',
    'MeasurementResult',
    'MeasurementResultList',
    'Meta',
    'MetaCategory',
    'MetaPattern',
    'MetaResult',
    'MetaTest',
    'Project',
    'Reference',
    'ResultClassification',
    'ResultStatus',
    'ResultType',
    'RunConclusion',
    'RunStatus',
    'RunStatusByUnexpected',
    'StampOrigin',
    'TaskExecution',
    'Test',
    'TestArgument',
    'TestIteration',
    'TestIterationRelation',
    'TestIterationResult',
    'User',
    'UserManager',
    'UserRoles',
    'View',
    'default_expected_for',
]
