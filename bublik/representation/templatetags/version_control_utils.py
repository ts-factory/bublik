# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import os.path

from django import template
from django.conf import settings
from git import Repo

from bublik.core.datetime_formatting import display_to_date_in_words


register = template.Library()


@register.simple_tag
def version_control_data():
    response = {}
    branch_name = 'Unknown branch!'
    repo = Repo(settings.BASE_DIR)

    try:
        branch_name = repo.active_branch.name

        response['url'] = os.path.join(
            repo.remote().url.rstrip('.git'),
            'commits/branch',
            branch_name,
        )
    except Exception:
        pass

    try:
        latest_commit = repo.head.commit
        latest_commit_hash = str(latest_commit)[:7]
        latest_commit_date = display_to_date_in_words(latest_commit.committed_datetime.date())

        response['summary'] = latest_commit.summary
        response['info'] = f'({branch_name} : {latest_commit_hash}) : {latest_commit_date}'
    except Exception:
        response['summary'] = '?'
        response['info'] = f'({branch_name} : ?) : ?'

    return response
