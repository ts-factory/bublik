# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.

import io
import json
from pathlib import Path
import tempfile
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase

from bublik.ai import models_dev
from bublik.ai.discovery import md_provider, models_from_models_dev


_CATALOGUE = {
    'acme': {
        'id': 'acme',
        'name': 'Acme',
        'api': 'https://api.acme.test/v1',
        'doc': 'https://acme.test/docs',
        'env': ['ACME_API_KEY'],
        'npm': '@ai-sdk/acme',
        'models': {
            'acme-1': {
                'id': 'acme-1',
                'name': 'Acme One',
                'tool_call': True,
                'reasoning': False,
                'cost': {'input': 1, 'output': 2},
                'limit': {'context': 1000, 'input': 900, 'output': 100},
                'modalities': {'input': ['text', 'image'], 'output': ['text']},
            },
            'acme-bare': {'id': 'acme-bare'},
        },
    },
}


class SnapshotTest(SimpleTestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / 'models_dev.json'
        patcher = mock.patch.object(models_dev, 'SNAPSHOT_PATH', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(models_dev.load_providers.cache_clear)

    def test_keeps_only_the_fields_bublik_reads(self):
        models_dev.write_snapshot(_CATALOGUE)
        stored = json.loads(self.path.read_text())
        self.assertNotIn('npm', stored['acme'])
        self.assertEqual(
            stored['acme']['models']['acme-1'],
            {
                'name': 'Acme One',
                'tool_call': True,
                'reasoning': False,
                'limit': {'context': 1000, 'output': 100},
                'modalities': {'input': ['text', 'image'], 'output': ['text']},
            },
        )

    def test_same_catalogue_gives_same_bytes(self):
        models_dev.write_snapshot(_CATALOGUE)
        first = self.path.read_bytes()
        models_dev.write_snapshot(json.loads(json.dumps(_CATALOGUE)))
        self.assertEqual(first, self.path.read_bytes())

    def test_a_model_takes_one_line(self):
        catalogue = {
            'beta': {'models': {}},
            'acme': {'id': 'acme', 'models': {'b': {'name': 'B'}, 'a': {'name': 'A'}}},
        }
        text = models_dev.dump(catalogue)
        self.assertEqual(json.loads(text), catalogue)
        self.assertEqual(
            text.splitlines(),
            [
                '{',
                ' "acme": {"id": "acme", "models": {',
                '  "a": {"name": "A"},',
                '  "b": {"name": "B"}',
                ' }},',
                ' "beta": {"models": {}}',
                '}',
            ],
        )

    def test_discovery_reads_the_snapshot(self):
        models_dev.write_snapshot(_CATALOGUE)
        provider = md_provider('acme')
        self.assertEqual(provider.env, ('ACME_API_KEY',))
        model = provider.models['acme-1']
        self.assertEqual(model.limit.output, 100)
        self.assertEqual(model.modalities.input, ('text', 'image'))
        bare = provider.models['acme-bare']
        self.assertIsNone(bare.limit)
        self.assertIsNone(bare.modalities)
        entries = {entry.id: entry for entry in models_from_models_dev('acme')}
        self.assertEqual(entries['acme-1'].name, 'Acme One')
        self.assertEqual(entries['acme-bare'].name, 'acme-bare')

    def test_sync_command_refreshes_snapshot_and_schema(self):
        source = self.path.parent / 'api.json'
        source.write_text(json.dumps(_CATALOGUE))
        schema = self.path.parent / 'ai.json'
        schema.write_text(
            json.dumps(
                {
                    'properties': {
                        'providers': {
                            'items': {'properties': {'id': {'anyOf': []}}},
                        },
                    },
                },
            ),
        )
        out = io.StringIO()
        with mock.patch(
            'bublik.interfaces.management.commands.ai_models_dev.SCHEMA_PATH',
            schema,
        ):
            call_command('ai_models_dev', sync=True, source=str(source), stdout=out)
        self.assertIn('1 providers, 2 models', out.getvalue())
        self.assertEqual(list(models_dev.load_providers()), ['acme'])
        enum = json.loads(schema.read_text())['properties']['providers']['items']
        self.assertEqual(enum['properties']['id']['anyOf'][0]['enum'], ['acme'])
