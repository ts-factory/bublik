# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
The models.dev catalogue snapshot shipped with Bublik.

https://models.dev/api.json is trimmed to the fields Bublik reads and kept in
``bublik/data/models_dev.json``, so model metadata needs no network access.

The snapshot is a part of the sources. ``manage.py ai_models_dev --sync``
refreshes it in a development checkout, and the result is committed. It is
not meant for a deployed instance: the working tree would be left changed,
and the running services keep the snapshot they loaded until restarted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import json
from pathlib import Path

import httpx

import bublik.data


API_URL = 'https://models.dev/api.json'
SNAPSHOT_PATH = Path(bublik.data.__file__).parent / 'models_dev.json'

_PROVIDER_FIELDS = ('id', 'name', 'api', 'doc', 'env')
_MODEL_FIELDS = ('name', 'tool_call', 'reasoning')
_LIMIT_FIELDS = ('context', 'output')
_MODALITY_FIELDS = ('input', 'output')


@dataclass(frozen=True)
class Limit:
    context: int | None = None
    output: int | None = None


@dataclass(frozen=True)
class Modalities:
    input: tuple[str, ...] = ()
    output: tuple[str, ...] = ()


@dataclass(frozen=True)
class Model:
    id: str
    name: str | None = None
    tool_call: bool | None = None
    reasoning: bool | None = None
    limit: Limit | None = None
    modalities: Modalities | None = None


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    api: str | None = None
    doc: str | None = None
    env: tuple[str, ...] = ()
    models: dict[str, Model] = field(default_factory=dict)


def _pick(data: dict, fields: tuple[str, ...]) -> dict:
    return {key: data[key] for key in fields if data.get(key) is not None}


def trim(catalogue: dict) -> dict:
    """The models.dev catalogue cut down to the fields Bublik reads."""
    trimmed = {}
    for provider_id, provider in catalogue.items():
        models = {}
        for model_id, model in provider.get('models', {}).items():
            entry = _pick(model, _MODEL_FIELDS)
            if limit := _pick(model.get('limit') or {}, _LIMIT_FIELDS):
                entry['limit'] = limit
            if modalities := _pick(model.get('modalities') or {}, _MODALITY_FIELDS):
                entry['modalities'] = modalities
            models[model_id] = entry
        trimmed[provider_id] = {**_pick(provider, _PROVIDER_FIELDS), 'models': models}
    return trimmed


def fetch(source: str = API_URL) -> dict:
    """The models.dev catalogue from a URL or a local copy of api.json."""
    if source.startswith(('http://', 'https://')):
        response = httpx.get(source, timeout=60, follow_redirects=True)
        response.raise_for_status()
        return response.json()
    return json.loads(Path(source).read_text(encoding='utf-8'))


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def dump(catalogue: dict) -> str:
    """
    A trimmed catalogue as JSON with a line for each model, so that a sync
    shows in a diff as the models it added, changed and removed. The same
    catalogue always gives the same text.
    """
    providers = []
    for provider_id, provider in sorted(catalogue.items()):
        head = _dumps({key: value for key, value in provider.items() if key != 'models'})
        models = ',\n'.join(
            f'  {_dumps(model_id)}: {_dumps(model)}'
            for model_id, model in sorted(provider['models'].items())
        )
        opening = f'{head[:-1]}, "models": {{' if head != '{}' else '{"models": {'
        body = f'\n{models}\n ' if models else ''
        providers.append(f' {_dumps(provider_id)}: {opening}{body}}}}}')
    return '{\n' + ',\n'.join(providers) + '\n}\n'


def write_snapshot(catalogue: dict, path: Path | None = None) -> None:
    """Store the trimmed catalogue."""
    (path or SNAPSHOT_PATH).write_text(dump(trim(catalogue)), encoding='utf-8')
    load_providers.cache_clear()


def _model(model_id: str, data: dict) -> Model:
    limit = data.get('limit')
    modalities = data.get('modalities')
    return Model(
        id=model_id,
        name=data.get('name'),
        tool_call=data.get('tool_call'),
        reasoning=data.get('reasoning'),
        limit=Limit(**_pick(limit, _LIMIT_FIELDS)) if limit else None,
        modalities=(
            Modalities(**{key: tuple(modalities.get(key) or ()) for key in _MODALITY_FIELDS})
            if modalities
            else None
        ),
    )


@lru_cache(maxsize=1)
def load_providers(path: Path | None = None) -> dict[str, Provider]:
    """Provider id -> provider, read from the snapshot (cached)."""
    catalogue = json.loads((path or SNAPSHOT_PATH).read_text(encoding='utf-8'))
    return {
        provider_id: Provider(
            id=data.get('id', provider_id),
            name=data.get('name', provider_id),
            api=data.get('api'),
            doc=data.get('doc'),
            env=tuple(data.get('env') or ()),
            models={
                model_id: _model(model_id, model)
                for model_id, model in data.get('models', {}).items()
            },
        )
        for provider_id, data in catalogue.items()
    }
