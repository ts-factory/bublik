# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

from __future__ import annotations

from typing import ClassVar

from django.core.exceptions import ObjectDoesNotExist
from django.template.loader import render_to_string
from rest_framework.exceptions import ValidationError

from bublik.core.exceptions import NotFoundError
from bublik.core.run.stats import get_run_conclusion, get_run_stats
from bublik.data import models
from bublik.data.models import TestIterationResult
from bublik.data.serializers import ProjectSerializer


class ProjectService:
    @staticmethod
    def list_projects_queryset():
        '''
        Get all projects queryset ordered by ID.

        Returns:
            QuerySet of Project objects
        '''
        return models.Project.objects.order_by('id')

    @staticmethod
    def list_projects() -> list[dict]:
        '''
        List all projects.

        Returns:
            List of dictionaries with project id and name
        '''
        return list(ProjectService.list_projects_queryset().values('id', 'name'))

    @staticmethod
    def get_project_instance(project_id: int | str) -> models.Project:
        '''
        Get a single project model instance by ID.

        Args:
            project_id: The ID of the project

        Returns:
            Project model instance

        Raises:
            NotFoundError: if project not found
        '''
        try:
            return models.Project.objects.get(id=project_id)
        except (ObjectDoesNotExist, TypeError, ValueError) as e:
            msg = f'Project {project_id} not found'
            raise NotFoundError(msg) from e

    @staticmethod
    def get_project(project_id: int | str) -> dict:
        '''
        Get a single project by ID.

        Args:
            project_id: The ID of the project

        Returns:
            Dictionary with project id and name

        Raises:
            NotFoundError: if project not found
        '''
        project = ProjectService.get_project_instance(project_id)
        return {'id': project.id, 'name': project.name}

    @staticmethod
    def create_project(data: dict) -> models.Project:
        '''
        Create a project.

        Args:
            data: Project payload

        Returns:
            Created Project model instance
        '''
        serializer = ProjectSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    @staticmethod
    def update_project(
        project_id: int | str,
        data: dict,
        partial: bool = False,
    ) -> models.Project:
        '''
        Update a project.

        Args:
            project_id: The ID of the project
            data: Project payload
            partial: Whether to perform partial update

        Returns:
            Updated Project model instance

        Raises:
            NotFoundError: if project not found
        '''
        project = ProjectService.get_project_instance(project_id)
        serializer = ProjectSerializer(project, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        return serializer.save()

    @staticmethod
    def delete_project(project_id: int | str) -> None:
        '''
        Delete a project.

        Args:
            project_id: The ID of the project

        Raises:
            NotFoundError: if project not found
            ValidationError: if project has linked runs
        '''
        project = ProjectService.get_project_instance(project_id)
        if project.testiterationresult_set.exists():
            msg = 'Unable to delete: there are runs linked to this project'
            raise ValidationError(msg)
        project.delete()


class ProjectBadgeService:
    # Thresholds for badge color logic
    _UNEXPECTED_WARNING_THRESHOLD = 0.2
    _RATE_PASSING_THRESHOLD = 90
    _RATE_WARNING_THRESHOLD = 70

    # Hex values taken from shields.io's named palette (CC0 1.0):
    # https://github.com/badges/shields/blob/master/badge-maker/lib/color.js
    COLORS: ClassVar[dict] = {
        'passing': '#4c1',
        'failing': '#e05d44',
        'warning': '#fe7d37',
        'pending': '#dfb317',
        'unknown': '#9f9f9f',
        'info': '#007ec6',
    }

    # Per-character width table for Verdana 11px.
    # Verdana was chosen by shields.io because it is the standard font for
    # flat-style SVG badges (see badge-renderers.js: WIDTH_FONT = '11px Verdana')
    # and was designed specifically for screen readability at small sizes.
    # 11px is the size that produces the classic badge appearance used across
    # the open-source ecosystem.
    #
    # Derived from the anafanafo package (MIT, Copyright 2018 Metabolize LLC),
    # used by shields.io badge-maker (CC0 1.0) to measure text widths
    # server-side without a browser or canvas:
    # https://github.com/metabolize/anafanafo/blob/main/packages/anafanafo/data/verdana-11px-normal.json
    #
    # Used to approximate text width server-side so that each badge section
    # can be sized correctly.  The values are in units of 1/10 px at
    # font-size="110"; the SVG uses transform="scale(.1)" to render them
    # at the actual 11 px size.
    _WIDTHS: ClassVar[dict] = {
        ' ': 3.87,
        '!': 4.33,
        '"': 5.05,
        '#': 9.0,
        '$': 6.99,
        '%': 11.84,
        '&': 7.99,
        "'": 2.95,
        '(': 5.0,
        ')': 5.0,
        '*': 6.99,
        '+': 9.0,
        ',': 4.0,
        '-': 5.0,
        '.': 4.0,
        '/': 5.0,
        '0': 6.99,
        '1': 6.99,
        '2': 6.99,
        '3': 6.99,
        '4': 6.99,
        '5': 6.99,
        '6': 6.99,
        '7': 6.99,
        '8': 6.99,
        '9': 6.99,
        ':': 5.0,
        ';': 5.0,
        '<': 9.0,
        '=': 9.0,
        '>': 9.0,
        '?': 6.0,
        '@': 11.0,
        'A': 7.52,
        'B': 7.54,
        'C': 7.68,
        'D': 8.48,
        'E': 6.96,
        'F': 6.32,
        'G': 8.53,
        'H': 8.27,
        'I': 4.63,
        'J': 5.0,
        'K': 7.62,
        'L': 6.12,
        'M': 9.27,
        'N': 8.23,
        'O': 8.66,
        'P': 6.63,
        'Q': 8.66,
        'R': 7.65,
        'S': 7.52,
        'T': 6.78,
        'U': 8.05,
        'V': 7.52,
        'W': 10.88,
        'X': 7.54,
        'Y': 6.77,
        'Z': 7.54,
        '[': 5.0,
        '\\': 5.0,
        ']': 5.0,
        '^': 9.0,
        '_': 6.99,
        '`': 6.99,
        'a': 6.61,
        'b': 6.85,
        'c': 5.73,
        'd': 6.85,
        'e': 6.55,
        'f': 3.87,
        'g': 6.85,
        'h': 6.96,
        'i': 3.02,
        'j': 3.79,
        'k': 6.51,
        'l': 3.02,
        'm': 10.7,
        'n': 6.96,
        'o': 6.68,
        'p': 6.85,
        'q': 6.85,
        'r': 4.69,
        's': 5.73,
        't': 4.33,
        'u': 6.96,
        'v': 6.51,
        'w': 9.0,
        'x': 6.51,
        'y': 6.51,
        'z': 5.78,
        '{': 6.98,
        '|': 5.0,
        '}': 6.98,
        '~': 9.0,
    }

    @staticmethod
    def _text_width(text):
        '''Return approximate pixel width of text rendered in Verdana 11px.'''
        return sum(ProjectBadgeService._WIDTHS.get(c, 6.0) for c in text)

    @staticmethod
    def render(label, value, color, label_color='#555') -> str:
        '''Generate an SVG badge matching shields.io flat style.'''
        label_tw = ProjectBadgeService._text_width(label)
        value_tw = ProjectBadgeService._text_width(value)

        # Badge section widths: textWidth + 10px padding each side
        label_width = int(label_tw + 10)
        value_width = int(value_tw + 10)
        total_width = label_width + value_width

        # Text x-centers and textLength values (x10 because of scale(.1))
        label_x = int(label_width / 2 * 10)
        value_x = int((label_width + value_width / 2) * 10)
        label_tl = int(label_tw * 10)
        value_tl = int(value_tw * 10)

        return render_to_string(
            'bublik/badge.svg.template',
            {
                'label': label,
                'value': value,
                'color': color,
                'label_color': label_color,
                'total_width': total_width,
                'label_width': label_width,
                'value_width': value_width,
                'label_x': label_x,
                'value_x': value_x,
                'label_tl': label_tl,
                'value_tl': value_tl,
            },
        )

    @staticmethod
    def get_latest_run(project):
        '''Return the most recent top-level run for the given project, or None.'''
        return (
            TestIterationResult.objects.filter(project=project, test_run__isnull=True)
            .order_by('-start')
            .first()
        )

    @staticmethod
    def run_conclusion(run) -> tuple:
        """Return (status_text, color) for a run using Bublik's built-in logic."""
        try:
            conclusion, _ = get_run_conclusion(run)
        except Exception:
            return 'unknown', ProjectBadgeService.COLORS['unknown']

        return {
            'run-ok': ('passing', ProjectBadgeService.COLORS['passing']),
            'run-running': ('running', ProjectBadgeService.COLORS['info']),
            'run-warning': ('warning', ProjectBadgeService.COLORS['warning']),
            'run-error': ('failing', ProjectBadgeService.COLORS['failing']),
            'run-stopped': ('stopped', ProjectBadgeService.COLORS['unknown']),
            'run-busy': ('busy', ProjectBadgeService.COLORS['pending']),
            'run-compromised': ('compromised', ProjectBadgeService.COLORS['warning']),
            'run-interrupted': ('interrupted', ProjectBadgeService.COLORS['warning']),
        }.get(conclusion, ('unknown', ProjectBadgeService.COLORS['unknown']))

    @staticmethod
    def _rate_badge(passed, total) -> tuple:
        """Return (value, color) for the 'rate' metric."""
        if total == 0:
            return 'N/A', ProjectBadgeService.COLORS['unknown']
        rate = passed / total * 100
        if rate >= ProjectBadgeService._RATE_PASSING_THRESHOLD:
            color = ProjectBadgeService.COLORS['passing']
        elif rate >= ProjectBadgeService._RATE_WARNING_THRESHOLD:
            color = ProjectBadgeService.COLORS['warning']
        else:
            color = ProjectBadgeService.COLORS['failing']
        return f'{rate:.1f}%', color

    @staticmethod
    def badge_content(run, metric: str) -> tuple:
        """
        Given a run (may be None) and metric string, return (value, color).

        If run is None, returns ('no runs', gray color).
        """
        if run is None:
            return 'no runs', ProjectBadgeService.COLORS['unknown']

        try:
            stats = get_run_stats(run.id)
        except Exception:
            return 'error', ProjectBadgeService.COLORS['failing']

        total = stats.get('total', 0)
        unexpected = stats.get('unexpected', 0)
        passed = total - unexpected

        if metric == 'passed':
            return str(passed), ProjectBadgeService.COLORS['passing']

        if metric == 'unexpected':
            if total == 0:
                color = ProjectBadgeService.COLORS['unknown']
            elif unexpected == 0:
                color = ProjectBadgeService.COLORS['passing']
            elif unexpected / total < ProjectBadgeService._UNEXPECTED_WARNING_THRESHOLD:
                color = ProjectBadgeService.COLORS['warning']
            else:
                color = ProjectBadgeService.COLORS['failing']
            return str(unexpected), color

        if metric == 'total':
            return str(total), ProjectBadgeService.COLORS['info']

        if metric == 'rate':
            return ProjectBadgeService._rate_badge(passed, total)

        # default: show run conclusion + unexpected count
        status_text, status_color = ProjectBadgeService.run_conclusion(run)
        value = f'{status_text} ({unexpected} nok)' if unexpected > 0 else status_text
        return value, status_color
