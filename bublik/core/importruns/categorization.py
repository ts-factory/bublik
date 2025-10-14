# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.
#
# This module works with categorizing metas on the core.importruns level.

import logging
import re

from django.core.management import call_command

from bublik.core.cache import set_tags_categories_cache
from bublik.core.importruns import get_or_none
from bublik.core.run.metadata import MetaData
from bublik.data.models import Meta, MetaCategory, MetaPattern, Project


logger = logging.getLogger('bublik.server')


def categorize_metas(meta_data: MetaData, project_id) -> None:
    '''
    Checks if all of the received Meta objects have already been categorized -
    which means if each Meta object has been added in MetaCategory.metas.
    If there is a Meta object that is not in proper MetaCategory.metas -
    the Meta object is added in MetaPattern.categories.metas.
    '''

    logger.info('Checking what metas are categorized already.')

    if (
        not MetaCategory.objects.filter(project_id=project_id).exists()
        or not MetaPattern.objects.filter(category__project_id=project_id).exists()
    ):
        project_name = Project.objects.get(id=project_id).name
        logger.info(
            'Calling meta_categorization command because there are no '
            'MetaCategory objects or MetaPattern objects '
            f'for the {project_name} project.',
        )
        call_command('meta_categorization', '--project_name', project_name)
        return

    metapatterns = MetaPattern.objects.filter(category__project_id=project_id)

    for m_data in meta_data.metas:
        meta = get_or_none(Meta.objects, **m_data)
        if meta is None:
            logger.debug(f'No existing metas for {m_data}.')
            continue
        logger.debug(f'Inspecting meta {meta.name}: {meta.value}.')

        for metapattern in metapatterns:
            if (
                re.search(metapattern.pattern, meta.name)
                and (
                    meta.pk not in metapattern.category.metas.all().values_list('pk', flat=True)
                )
                and (meta.type == metapattern.category.type)
            ):
                logger.info(
                    f'Adding ({meta.name}:{meta.value}) to the MetaPattern '
                    f'{metapattern.pattern} and MetaCategory '
                    f'{metapattern.category}.',
                )
                metapattern.category.metas.add(meta)

    # Since tags are also Meta objects but are processed differently - we need
    # to update cached tags
    set_tags_categories_cache(project_id)
    logger.info('Update cached tags.')
