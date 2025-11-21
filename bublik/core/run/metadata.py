# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2016-2023 OKTET Labs Ltd. All rights reserved.

import json
import os
import shlex
import subprocess

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import F, Q
import pendulum

from bublik.core.config.services import ConfigServices
from bublik.core.datetime_formatting import date_str_to_db
from bublik.core.exceptions import ImportrunsError
from bublik.core.importruns.utils import measure_time
from bublik.core.logging import get_task_or_server_logger
from bublik.core.meta.categorization import categorize_meta
from bublik.core.queries import get_or_none
from bublik.core.shortcuts import serialize
from bublik.core.utils import find_dict_in_list, get_difference
from bublik.data.models import GlobalConfigs, Meta, MetaResult, Project
from bublik.data.serializers import (
    MetaResultSerializer,
    MetaSerializer,
    ReferenceSerializer,
)


logger = get_task_or_server_logger()


class MetaData:
    '''
    This class responsibilities are bellow.
    1. Check:
        - metadata version,
        - format,
        - project matching,
        - key_metas for existence, satisfying Meta model and uniqueness.
    2. Process run start and finish to apply from/to importruns filter.
    3. Control timezone of datetime objects.
    4. Save necessary data to create a TestIterationResult minimal instance.
    5. Get or create / force update run metas.
    '''

    FMT_META_DATA_GENERATE = '{path_meta_data_script} --path {process_dir} --project {project}'

    def __init__(self, meta_data_json, project=None):
        super().__init__()
        self.project = project
        self.version = None
        self.run_start = None
        self.run_finish = None
        self.status_meta = None
        self.metas = []
        self.key_metas = []

        # Allow JSON input both as a string and as a dictionary
        if isinstance(meta_data_json, str):
            meta_data_json = json.loads(meta_data_json)

        self.__check_metadata(meta_data_json)
        self.__parse_timestamps()

    @staticmethod
    def load(meta_data_filename, project):
        with open(meta_data_filename) as meta_data_file:
            return MetaData(meta_data_file.read(), project)

    @staticmethod
    def generate(process_dir, project_name):
        logger.info(f'Generate meta_data.json at {process_dir}')
        try:
            path_meta_data_script = os.path.join(settings.PER_CONF_DIR, 'generate_metadata.py')
            cmd = shlex.split(
                MetaData.FMT_META_DATA_GENERATE.format(
                    path_meta_data_script=path_meta_data_script,
                    process_dir=process_dir,
                    project=project_name,
                ),
            )
            logger.info(f'running command: {cmd}')
            subprocess.run(cmd, stdout=subprocess.PIPE, check=False)

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            msg = f'Per-project generate_metadata.py has returned an error {e}'
            raise RuntimeError(
                msg,
            ) from RuntimeError

        return MetaData.load(os.path.join(process_dir, 'meta_data.json'))

    def __check_metadata(self, meta_data):
        # Check metadata version
        self.version = meta_data.get('version')
        if not self.version or (self.version and self.version > 1):
            msg = 'not valid version of meta_data.json'
            raise ValueError(msg)

        # Check format specific data
        self.metas = meta_data.get('metas')
        if not self.metas:
            msg = 'meta_data.json parser expected a list of metas'
            raise KeyError(msg)

        # Assign project from meta if not provided
        if not self.project:
            project_meta = find_dict_in_list({'name': 'PROJECT'}, self.metas)
            if not project_meta:
                msg = 'meta_data.json parser expected a PROJECT meta.'
                raise ValueError(msg)
            try:
                self.project = Project.objects.get(name=project_meta['value'])
            except Project.DoesNotExist as mdne:
                msg = (
                    f'the project does not exist: {project_meta["value"]}. '
                    'Create it to import logs.'
                )
                raise ObjectDoesNotExist(msg) from mdne

        # Check status meta
        run_status_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'RUN_STATUS_META',
            self.project.id,
        )
        if not find_dict_in_list({'name': run_status_meta}, self.metas):
            msg = 'There is no status meta in meta_data.json. It is a required meta.'
            raise ValueError(msg)

        key_metas_fields = set()
        key_metas_names = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'RUN_KEY_METAS',
            self.project.id,
        )

        # Check names duplicates in RUN_KEY_METAS
        if len(key_metas_names) != len(set(key_metas_names)):
            logger.warning(
                'duplicates in RUN_KEY_METAS are forbiden, '
                'check and fix this in the project per_conf',
            )
            key_metas_names = list(set(key_metas_names))

        # Check if all key metas are present in metadata
        for meta in self.metas:
            meta_name = meta.get('name')

            if not meta_name:
                logger.warning(
                    'every meta should have its [name] in metadata, '
                    f'check the following meta: {meta}',
                )
                continue

            if meta_name in key_metas_names:
                self.key_metas.append(meta)
                key_metas_fields = key_metas_fields.union(meta.keys())
                del key_metas_names[key_metas_names.index(meta_name)]

            # Check key metas duplicates
            elif find_dict_in_list({'name': meta_name}, self.key_metas):
                msg = (
                    f'the following key meta is duplicated: {meta_name}, '
                    'that compromises metadata, ignoring the run'
                )
                raise ValueError(msg)

        if key_metas_names:
            msg = (
                "can't identify the run, the following RUN_KEY_METAS are "
                f"absent in metadata: {','.join(key_metas_names)}"
            )
            raise AttributeError(msg)

        # Check if all key metas satisfy Meta model
        model_fields = [f.name for f in Meta._meta.get_fields()]
        diff = get_difference(key_metas_fields, model_fields)
        if diff:
            msg = f"meta can't have the following fields: {','.join(diff)}"
            raise ValueError(msg)

    def __parse_timestamps(self):
        start_meta = find_dict_in_list({'name': 'START_TIMESTAMP'}, self.metas)
        if start_meta and 'value' in start_meta:
            self.run_start = pendulum.parse(start_meta['value'])

        finish_meta = find_dict_in_list({'name': 'FINISH_TIMESTAMP'}, self.metas)
        if finish_meta and 'value' in finish_meta:
            self.run_finish = pendulum.parse(finish_meta['value'])

    def check_run_period(self, date_from, date_to):
        return not (
            self.run_start
            and self.run_finish
            and (
                self.run_start < date_from
                or self.run_start > date_to
                or self.run_finish > date_to
            )
        )

    def __preprocess_meta(self, m_data, to_data=False):
        dashboard_date_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'DASHBOARD_DATE',
            self.project.id,
        )
        name = m_data.get('name')
        value = m_data.get('value')

        if dashboard_date_meta and name == dashboard_date_meta:
            converted_value = date_str_to_db(value)
            if not converted_value:
                logger.warning(
                    f"can't parse meta for dashboard date: name={name}, value={value}",
                )

            m_data['value'] = converted_value

        m_data['type'] = m_data.get('type', 'label')

        reference = m_data.pop('reference', None)
        if reference:
            reference_data = {'name': name, 'uri': reference}
            if to_data:
                return reference_data
            reference_serializer = serialize(ReferenceSerializer, reference_data, logger)
            reference, _ = reference_serializer.get_or_create()
        return reference

    def get_or_create_metas(self, run):
        status_meta = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'RUN_STATUS_META',
            self.project.id,
        )

        for m_data in self.metas:
            name = m_data.get('name')
            value = m_data.get('value')

            if name is None:
                continue

            if not name and not value:
                logger.warning(f"meta with empty 'name' and 'value' can't be saved: {m_data}")
                continue

            reference = self.__preprocess_meta(m_data)

            meta_serializer = serialize(MetaSerializer, m_data, logger)
            meta, created = meta_serializer.get_or_create()
            if created:
                categorize_meta(meta)

            if status_meta and name == status_meta:
                logger.info(f'the run status is {meta.value}')
                MetaResult.objects.update_or_create(
                    result=run,
                    meta__name=name,
                    defaults={'meta': meta},
                )
                self.status_meta = m_data
            else:
                MetaResult.objects.get_or_create(meta=meta, result=run, reference=reference)

            logger.debug(
                f'run meta: {meta.type!s:<13} {meta.name!s:<15} = {meta.value!s:<}',
            )

        return True

    def force_update_metas(self, run):
        new = []
        matched = []

        # Metas storing an import id and tags are not a part of metadata.
        # Therefore, they are excluded from the metadata processing statistics.
        existing = MetaResult.objects.filter(
            Q(result=run) & ~Q(meta__type__in=('import', 'tag')),
        )
        deleted = existing._clone()

        for m_data in self.metas:
            name = m_data.get('name')
            value = m_data.get('value')

            if name is None:
                continue

            if not name and not value:
                logger.warning(f"meta with empty 'name' and 'value' can't be saved: {m_data}")
                continue

            reference_data = self.__preprocess_meta(m_data, to_data=True)
            meta = get_or_none(Meta.objects, **m_data)
            meta_result = get_or_none(
                MetaResult.objects,
                meta=meta,
                reference=reference_data,
                result=run,
            )

            if meta_result:
                matched.append(meta_result.id)
                deleted = deleted.exclude(id=meta_result.id)
            else:
                mr_data = {'meta': m_data, 'result': run.pk, 'reference': reference_data}
                mr_serializer = serialize(MetaResultSerializer, mr_data, logger)
                new.append(mr_serializer)

        logger.info(
            f"run's metadata: existing {len(existing)}, incoming {len(self.metas)}, "
            f'matched {len(matched)}, new {len(new)}, deleted {len(deleted)}.',
        )

        logger.info(
            f"deleted meta results: {list(deleted.values_list('meta__name', 'meta__value'))}",
        )

        deleted.delete()

        created = []
        for mr_serializer in new:
            meta_result, _ = mr_serializer.get_or_create()
            created.append(meta_result.meta)

        logger.info(f'new meta results: {created}')

        return True

    def check_if_essential_metas_changed(self, run):
        run_key_metas = ConfigServices.getattr_from_global(
            GlobalConfigs.PER_CONF.name,
            'RUN_KEY_METAS',
            self.project.id,
        )
        essential_meta_names = [*run_key_metas, 'PROJECT']

        essential_metas = MetaResult.objects.filter(
            Q(result=run) & Q(meta__name__in=essential_meta_names),
        ).values_list(F('meta__name'), F('meta__value'))

        for name, value in essential_metas:
            if not find_dict_in_list({'name': name, 'value': value}, self.metas):
                msg = f'broken essential meta: {name} = {value}'
                raise ImportrunsError(
                    message=msg,
                )

    @measure_time('processing meta data')
    def handle(self, run, force_update=False):
        self.check_if_essential_metas_changed(run)

        if force_update:
            return self.force_update_metas(run)
        return self.get_or_create_metas(run)
