# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from rest_framework.routers import DefaultRouter


class ActionsOnlyRouter(DefaultRouter):
    '''
    A router for APIs providing only custom endpoints which are generated
    by @action() on the viewset.

    Forbids default methods such as:
    list, retrieve, create, update, partial_update, and destroy
    disabling root view generation.
    '''

    include_root_view = False
