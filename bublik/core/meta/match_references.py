# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs


def build_revision_references(revisions):
    revision_groups_list = []
    for revision in revisions:
        reference = ConfigServices.getattr_from_global(
            GlobalConfigs.REFERENCES.name,
            'REVISIONS',
        ).get(revision['name'])
        revision_url = ''

        if reference:
            revision_url_subpath = reference.get('reference_subpath', '/commit/')
            revision_url = str(reference['uri'][0]) + revision_url_subpath + revision['value']

        revision_groups_list.append(
            {
                'name': revision['name'],
                'value': revision['value'],
                'url': revision_url,
            },
        )

    return revision_groups_list
