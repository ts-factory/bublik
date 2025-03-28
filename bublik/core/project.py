# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from contextvars import ContextVar

from bublik.data import models


project_var = ContextVar('project', default=None)


def set_current_project(project):
    '''
    Store the current project in the context variable.
    '''
    # check whether such a project exists
    if project is not None:
        models.Meta.projects.get(id=project)
    project_var.set(project)


def get_current_project():
    '''
    Get the project from the context variable.
    '''
    return project_var.get()
