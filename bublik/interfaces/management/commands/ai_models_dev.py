# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
models.dev utilities for the ``ai`` global config.

Usage::

    python manage.py ai_models_dev --list-providers
    python manage.py ai_models_dev --provider openai
    python manage.py ai_models_dev --provider openai --json
    python manage.py ai_models_dev --search gpt-5
    python manage.py ai_models_dev --generate-schema
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from bublik.ai.discovery import md_provider, md_providers, models_from_models_dev
import bublik.data


SCHEMA_PATH = Path(bublik.data.__file__).parent / 'schemas' / 'ai.json'


class Command(BaseCommand):
    help = 'models.dev integration utilities for the Bublik AI config.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--provider',
            default=None,
            help='models.dev provider id (e.g. openai, anthropic)',
        )
        parser.add_argument(
            '--json',
            action='store_true',
            default=False,
            help='Output the provider as an ai config snippet',
        )
        parser.add_argument(
            '--list-providers',
            action='store_true',
            default=False,
            help='List all models.dev providers with model counts',
        )
        parser.add_argument(
            '--search',
            default=None,
            help='Search all providers for a model id',
        )
        parser.add_argument(
            '--generate-schema',
            action='store_true',
            default=False,
            help='Refresh the models.dev provider-id enum in ai.json '
            '(enables editor autocomplete)',
        )

    def handle(self, **options):
        if options['list_providers']:
            self._list_providers()
        elif options['generate_schema']:
            self._generate_schema()
        elif options['provider']:
            self._show_provider(options['provider'], options['json'])
        elif options['search']:
            self._search_model(options['search'])
        else:
            self.print_help('manage.py', 'ai_models_dev')

    def _list_providers(self):
        providers = sorted(md_providers(), key=lambda p: p.id)
        self.stdout.write(
            self.style.MIGRATE_HEADING(f'models.dev providers ({len(providers)} total)'),
        )
        self.stdout.write(f'  {"ID":<32} {"Name":<32} Models')
        self.stdout.write('  ' + '-' * 76)
        for provider in providers:
            self.stdout.write(f'  {provider.id:<32} {provider.name:<32} {len(provider.models)}')

    def _show_provider(self, provider_id: str, as_json: bool):
        provider = md_provider(provider_id)
        if provider is None:
            self.stderr.write(
                self.style.ERROR(
                    f'Provider {provider_id!r} not found in models.dev. '
                    f'Use --list-providers to see all available ids.',
                ),
            )
            return
        if provider.id != provider_id:
            self.stdout.write(
                self.style.WARNING(
                    f'Using models.dev provider {provider.id!r} for {provider_id!r}',
                ),
            )

        models = models_from_models_dev(provider.id)

        if as_json:
            snippet = {
                'id': provider.id,
                'type': 'openai',
                'name': provider.name,
                'models': [
                    model.model_dump(exclude_none=True, exclude_defaults=True)
                    for model in models
                ],
            }
            self.stdout.write(json.dumps(snippet, indent=2, ensure_ascii=False))
            return

        self.stdout.write(
            self.style.MIGRATE_HEADING(f'Provider: {provider.name} ({provider.id})'),
        )
        self.stdout.write(f'  Models:    {len(models)}')
        self.stdout.write(f'  Docs URL:  {provider.doc or "-"}')
        self.stdout.write(f'  Key env:   {", ".join(provider.env or ()) or "-"}')
        self.stdout.write('')
        self.stdout.write(
            f'  {"Model ID":<45} {"Name":<30} {"Context":<9} {"Output":<9} {"Tools":<6} Reason',
        )
        self.stdout.write('  ' + '-' * 106)
        for model in models:
            limit = model.limit
            self.stdout.write(
                f'  {model.id:<45} {(model.name or "-"):<30} '
                f'{(limit.context if limit else None) or "-":<9} '
                f'{(limit.output if limit else None) or "-":<9} '
                f'{"yes" if model.tool_call else "no":<6} '
                f'{"yes" if model.reasoning else "no"}',
            )

    def _search_model(self, query: str):
        query_lower = query.lower()
        found = [
            (provider.id, model_id, model.reasoning)
            for provider in sorted(md_providers(), key=lambda p: p.id)
            for model_id, model in sorted(provider.models.items())
            if query_lower in model_id.lower()
        ]
        if not found:
            self.stdout.write(f'No models found matching {query!r}')
            return
        shown = found[:50]
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f'Models matching {query!r} ({len(shown)} of {len(found)} shown)',
            ),
        )
        self.stdout.write(f'  {"Provider":<28} {"Model ID":<45} Reasoning')
        self.stdout.write('  ' + '-' * 84)
        for provider_id, model_id, reasoning in shown:
            self.stdout.write(
                f'  {provider_id:<28} {model_id:<45} {"yes" if reasoning else "no"}',
            )

    def _generate_schema(self):
        """Refresh the providers[].id anyOf-enum with the current models.dev ids."""
        schema = json.loads(SCHEMA_PATH.read_text())

        provider_ids = sorted(p.id for p in md_providers())
        id_property = (
            schema.get('properties', {})
            .get('providers', {})
            .get('items', {})
            .get('properties', {})
            .get('id')
        )
        if not id_property or 'anyOf' not in id_property:
            self.stderr.write(
                self.style.ERROR(f'providers[].id anyOf not found in {SCHEMA_PATH}'),
            )
            return
        id_property['anyOf'] = [
            {
                'enum': provider_ids,
                'description': 'Known models.dev provider',
            },
            {
                'type': 'string',
                'minLength': 1,
                'description': 'Custom gateway provider ID',
            },
        ]

        SCHEMA_PATH.write_text(json.dumps(schema, indent=4, ensure_ascii=False) + '\n')
        self.stdout.write(
            self.style.SUCCESS(
                f'Refreshed {len(provider_ids)} models.dev provider ids in {SCHEMA_PATH}',
            ),
        )
