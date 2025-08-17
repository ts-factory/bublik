# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025 OKTET Labs Ltd. All rights reserved.

from itertools import islice
import json

from django.core.exceptions import ObjectDoesNotExist
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.db.models.functions import Length

from bublik.core.shortcuts import serialize
from bublik.data.models import (
    Config,
    Meta,
    MetaTest,
    Project,
    TestIterationResult,
)
from bublik.data.serializers import (
    ConfigSerializer,
    MetaTestSerializer,
    ProjectSerializer,
)


class Command(BaseCommand):
    help = 'Reassign runs to projects based on the provided meta values'

    def add_arguments(self, parser):
        meta_names = list(Meta.objects.values_list('name', flat=True).distinct())
        parser.add_argument(
            '-m',
            '--meta',
            type=str,
            choices=meta_names,
            default='PROJECT',
            help='The meta name by which runs will be grouped into projects.',
        )

    def is_meta_valid(self, meta_name):
        self.stdout.write('Checking provided meta...')
        metas = Meta.objects.filter(name=meta_name)
        invalid_values = list(
            metas.annotate(val_len=Length('value'))
            .filter(Q(value__isnull=True) | Q(value='') | Q(val_len__gt=64))
            .values_list('value', flat=True)
            .distinct(),
        )
        if invalid_values:
            msg = (
                'Invalid meta name: not all meta values can be used as a project name '
                f'(some are blank or too long). Invalid values: {invalid_values}'
            )
            return False, msg

        runs_num = TestIterationResult.objects.filter(test_run__isnull=True).count()
        runs_with_single_meta = (
            metas.filter(metaresult__result__test_run__isnull=True)
            .values('metaresult__result')
            .annotate(meta_count=Count('id'))
            .filter(meta_count=1)
            .count()
        )
        if runs_with_single_meta != runs_num:
            msg = (
                'Invalid meta name: not all runs are linked '
                'to exactly one meta with this name.'
            )
            return False, msg

        msg = f'The {meta_name} meta can be used to reassign projects.'
        return True, msg

    def get_runs_to_migrate(self, meta_name):
        '''
        Returns a queryset of runs for which the name of the linked project
        does not match the value of the meta with the provided name associated with the run.
        Also includes runs that are not yet linked to any project.

        This helps identify runs that require project reassignment
        based on the provided meta value.
        '''
        self.stdout.write('Checking for runs requiring migration...')
        meta_value_subquery = Meta.objects.filter(
            metaresult__result=OuterRef('pk'),
            name=meta_name,
        ).values('value')[:1]

        return (
            TestIterationResult.objects.filter(test_run__isnull=True)
            .annotate(
                meta_value=Subquery(meta_value_subquery),
                project_name=F('project__name'),
            )
            .filter(
                Q(project_name__isnull=True)
                | (
                    Q(meta_value__isnull=False)
                    & Q(project_name__isnull=False)
                    & ~Q(meta_value=F('project_name'))
                ),
            )
        )

    def get_or_create_project(self, meta):
        '''
        Returns the Project instance with the specified name.
        If such a project does not exist, a new one is created.
        '''
        try:
            project = Project.objects.get(name=meta.value)
            self.stdout.write(f'\tProject {meta.value} already exist.')
        except ObjectDoesNotExist:
            self.stdout.write('\tCreating new project and configurations...')
            project_serializer = serialize(ProjectSerializer, {'name': meta.value})
            project = project_serializer.create(project_serializer.validated_data)
            self.stdout.write(
                self.style.SUCCESS(
                    f'\tProject {meta.value} successfully created!',
                ),
            )
        return project

    @transaction.atomic
    def init_project_configs(self, project, runs_to_migrate):
        '''
        Initializes configurations for the given project by copying active configurations
        from the projects associated with the runs being reassigned.

        If multiple configurations with the same type-name pair exist across
        source projects, they are considered conflicting and will be skipped.

        Only unique configurations (by content) are copied. Conflicting configurations
        are excluded from initialization, and a warning with the conflicting type-name
        pairs is printed.
        '''
        project_ids = list(runs_to_migrate.values_list('project', flat=True).distinct())

        project_filter = Q()
        non_null_ids = [pid for pid in project_ids if pid is not None]
        if non_null_ids:
            project_filter |= Q(project__in=non_null_ids)
        if None in project_ids:
            project_filter |= Q(project__isnull=True)

        project_configs = (
            Config.objects.filter(project_filter, is_active=True)
            .order_by('content')
            .distinct('content')
        )

        conflict_pairs = (
            Config.objects.filter(id__in=project_configs.values('id'))
            .values('type', 'name')
            .annotate(count=Count('id'))
            .filter(count__gt=1)
            .values_list('type', 'name')
        )

        if conflict_pairs:
            pairs_str = ', '.join(f'{t}-{n}' for t, n in conflict_pairs)
            self.stdout.write(
                self.style.WARNING(
                    '\tThe following configuration type-name pairs have cross-project '
                    f'conflicts and will not be initialized: {pairs_str}',
                ),
            )
            conflict_filter = Q()
            conflict_filter |= Q(
                *(Q(type=t, name=n) for t, n in conflict_pairs),
                _connector=Q.OR,
            )
            project_configs = project_configs.exclude(conflict_filter)

        for project_config in project_configs:
            ConfigSerializer.initialize(
                {
                    'type': project_config.type,
                    'name': project_config.name,
                    'project': project,
                    'description': project_config.description,
                    'content': project_config.content,
                },
            )
        self.stdout.write(
            self.style.SUCCESS(
                f'\tConfigs for {project.name} successfully initialized!',
            ),
        )

    @transaction.atomic
    def update_project_test_comments(self, project, runs_to_migrate_ids):
        '''
        Extends the test comments of the given project with those linked to the provided runs.
        '''
        self.stdout.write('\tMigrate test comments:')
        meta_test_comments = (
            MetaTest.objects.filter(
                meta__type='comment',
                test__testiteration__testiterationresult__test_run__in=runs_to_migrate_ids,
            )
            .order_by('test', 'meta', 'project', 'serial')
            .distinct('test', 'meta')
            .select_related('test', 'meta')
        )
        self.stdout.write(
            f'\tThe number of test comments to migrate: {meta_test_comments.count()}',
        )
        for mtc in meta_test_comments:
            serializer = MetaTestSerializer(
                data={'comment': json.loads(mtc.meta.value)},
                context={'test': mtc.test, 'serial': mtc.serial, 'project': project},
            )
            if serializer.is_valid():
                serializer.save()

        self.stdout.write(
            self.style.SUCCESS('\tTest comments successfully migrated!'),
        )

    def reassign_runs_to_project(self, project, runs_to_migrate_ids):
        '''
        Assigns the specified runs and their linked results to the given project.

        Processes the update in batches to optimize performance and ensure atomicity.
        '''
        self.stdout.write('\tMigrate runs:')
        self.stdout.write(
            f'\tThe number of runs to migrate: {len(runs_to_migrate_ids)}',
        )

        self.stdout.write('\tMigrating...')
        batch_size = 20_000
        run_ids_iterator = iter(runs_to_migrate_ids)
        migrated_count = 0

        while True:
            run_ids_batch = list(islice(run_ids_iterator, batch_size))
            if not run_ids_batch:
                break
            with transaction.atomic():
                TestIterationResult.objects.filter(id__in=run_ids_batch).update(project=project)
                TestIterationResult.objects.filter(test_run_id__in=run_ids_batch).update(
                    project=project,
                )
            migrated_count += len(run_ids_batch)
            self.stdout.write(f'\tMigrated {len(run_ids_batch)} runs (total: {migrated_count})')

        self.stdout.write(self.style.SUCCESS('\tRuns successfully migrated!'))

    def handle(self, *args, **options):
        self.stdout.write('Migrating runs to projects based on associated metas:')

        meta_name = options['meta']

        # Check passed meta name
        is_meta_valid, msg = self.is_meta_valid(meta_name)
        if not is_meta_valid:
            self.stdout.write(
                self.style.ERROR(msg),
            )
            return
        self.stdout.write(self.style.SUCCESS(msg))

        # Identify runs that need to be reassigned based on meta
        runs_to_migrate = self.get_runs_to_migrate(meta_name)
        if not runs_to_migrate.exists():
            self.stdout.write('Already migrated.')
            return
        self.stdout.write(
            self.style.SUCCESS(f'There are {runs_to_migrate.count()} runs to be migrated.'),
        )

        is_initial = not Project.objects.exists()

        # Initialize projects and configs from meta, copy test comments, reassign runs
        metas_with_runs = Meta.objects.filter(
            name=meta_name,
            metaresult__isnull=False,
        ).distinct()
        metas_with_runs_values = list(metas_with_runs.values_list('value', flat=True))
        self.stdout.write(
            f'{meta_name} values: {metas_with_runs_values}',
        )
        for meta in metas_with_runs:
            self.stdout.write(f'Processing {meta.value} {meta_name}:')

            meta_runs_to_migrate = runs_to_migrate.filter(
                meta_results__meta__name=meta_name,
                meta_results__meta__value=meta.value,
            )
            if not meta_runs_to_migrate.exists():
                self.stdout.write('\tRuns already migrated.')
                continue

            meta_runs_to_migrate_ids = list(
                meta_runs_to_migrate.values_list('id', flat=True),
            )

            # Get or create project
            project = self.get_or_create_project(meta)

            # Initialize project configs if absent
            if Config.objects.filter(project=project).exists():
                self.stdout.write(f'\tConfigs for {project.name} project already exist.')
            else:
                self.init_project_configs(project, meta_runs_to_migrate)

            self.update_project_test_comments(project, meta_runs_to_migrate_ids)

            # Reassign runs to project
            self.reassign_runs_to_project(project, meta_runs_to_migrate_ids)

        if is_initial:
            Config.objects.filter(project__isnull=True).delete()
            MetaTest.objects.filter(project__isnull=True).delete()
            call_command('meta_categorization')
