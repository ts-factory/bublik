# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Model metadata discovery for the ``ai`` config.

models.dev (via the ``models-dev`` package, an offline snapshot of
https://models.dev) is the source of model metadata: display name, token
limits, modalities, tool-calling and reasoning support. A provider's model
list is populated with the following priority:

1. an explicit ``models`` list in the config — kept as-is, unset fields
   enriched from models.dev (explicit values always win);
2. an ``api_url`` on an OpenAI-protocol/anthropic provider — HTTP discovery
   via ``GET {api_url}/models`` (the gateway is authoritative for *which*
   models exist; models.dev only fills metadata, never the display name);
3. the provider ``id`` matching a models.dev provider — its full catalogue.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import logging
from typing import TYPE_CHECKING

from django.core.cache import caches
import httpx
from models_dev import get_provider as _md_get_provider
from models_dev import providers as _md_providers

from bublik.ai.types import Limit, Modalities, ModelEntry


if TYPE_CHECKING:
    from models_dev import Model as MdModel
    from models_dev import Provider as MdProvider

    from bublik.ai.types import Provider


logger = logging.getLogger(__name__)

# Provider types speaking the OpenAI chat protocol (or the Anthropic Messages
# API); their gateways expose ``GET {api_url}/models``, so HTTP model
# auto-discovery is supported.
_DISCOVERABLE_TYPES = {
    'openai',
    'openai-chat',
    'openai-responses',
    'anthropic',
    'ollama',
    'openrouter',
    'litellm',
}

_DISCOVERY_TIMEOUT_S = 5
_DISCOVERY_TTL_S = 300
# Failed discovery is cached briefly so a down gateway is not hammered on
# every /chat/models request, yet recovers quickly once it is back.
_DISCOVERY_ERROR_TTL_S = 30

# Bublik provider ids/types whose models.dev provider id differs.
_MD_ID_ALIASES = {
    'bedrock': 'amazon-bedrock',
}


@lru_cache(maxsize=1)
def md_providers() -> list[MdProvider]:
    """All models.dev providers (cached for the process lifetime)."""
    return list(_md_providers())


@lru_cache(maxsize=256)
def md_provider(provider_id: str) -> MdProvider | None:
    """A models.dev provider by id (directly or via an alias), or ``None``."""
    for pid in (provider_id, _MD_ID_ALIASES.get(provider_id)):
        if not pid:
            continue
        try:
            return _md_get_provider(pid)
        except KeyError:
            continue
    return None


@lru_cache(maxsize=1)
def _global_model_index() -> dict[str, MdModel]:
    """model id -> models.dev Model across ALL providers.

    Used for custom gateways whose id is unknown to models.dev: the model ids
    they serve (e.g. ``deepseek-v4-flash``) usually exist under some models.dev
    provider. The alphabetically earliest provider wins, so lookups are
    deterministic.
    """
    index: dict[str, MdModel] = {}
    for provider in sorted(md_providers(), key=lambda p: p.id):
        for model_id, model in provider.models.items():
            index.setdefault(model_id, model)
    return index


def _find_md_model(provider: Provider, model_id: str) -> MdModel | None:
    """Look up a model in models.dev: provider id first, then type, then globally."""
    for pid in (provider.id, provider.type):
        md = md_provider(pid)
        if md is not None and model_id in md.models:
            return md.models[model_id]
    return _global_model_index().get(model_id)


def _md_limit(model: MdModel) -> Limit | None:
    if not model.limit:
        return None
    return Limit(context=model.limit.context, output=model.limit.output)


def _md_modalities(model: MdModel) -> Modalities | None:
    if not model.modalities:
        return None
    return Modalities(
        input=list(model.modalities.input or ()),
        output=list(model.modalities.output or ()),
    )


def _entry_from_md(model_id: str, model: MdModel) -> ModelEntry:
    return ModelEntry(
        id=model_id,
        name=model.name or model_id,
        limit=_md_limit(model),
        modalities=_md_modalities(model),
        tool_call=model.tool_call,
        reasoning=model.reasoning,
    )


def enrich_model(entry: ModelEntry, provider: Provider) -> ModelEntry:
    """Fill an entry's unset fields from models.dev; explicit values always win.

    A model unknown to models.dev is returned as-is (with ``name`` defaulted to
    the id), so custom gateways with in-house models keep working.
    """
    model = _find_md_model(provider, entry.id)
    update: dict = {}
    if entry.name is None:
        update['name'] = (model.name if model else None) or entry.id
    if model is not None:
        if entry.limit is None:
            update['limit'] = _md_limit(model)
        if entry.modalities is None:
            update['modalities'] = _md_modalities(model)
        if entry.tool_call is None:
            update['tool_call'] = model.tool_call
        if entry.reasoning is None:
            update['reasoning'] = model.reasoning
    return entry.model_copy(update=update) if update else entry


def _discovery_cache_key(provider: Provider) -> str:
    url_digest = hashlib.sha1((provider.api_url or '').encode()).hexdigest()
    return f'ai-models:{provider.id}:{url_digest}'


def _fetch_gateway_models(provider: Provider, api_key: str | None) -> list[dict]:
    """Fetch model ids (and optional display names) from a ``/models`` endpoint.

    Supports OpenAI-protocol providers (``Authorization: Bearer``) and the
    Anthropic Messages API (``x-api-key`` + ``anthropic-version``). Results
    (including failures, as an empty list) are cached; on failure the provider
    degrades to an empty model list rather than breaking /chat/models.
    """
    cache = caches['ai_models']
    cache_key = _discovery_cache_key(provider)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    api_url = (provider.api_url or '').rstrip('/')
    headers = {}
    params = {}
    if provider.type == 'anthropic':
        if api_key:
            headers['x-api-key'] = api_key
        headers['anthropic-version'] = '2023-06-01'
        params['limit'] = 1000
    elif api_key:
        headers['Authorization'] = f'Bearer {api_key}'

    if provider.type == 'anthropic':
        # The Anthropic Messages API versions its paths: the models list lives
        # at /v1/models. Config authors write api_url both with and without
        # the /v1 suffix (the SDK default base URL has none), so normalize.
        models_url = f'{api_url.removesuffix("/v1")}/v1/models'
    else:
        models_url = f'{api_url}/models'

    try:
        response = httpx.get(
            models_url,
            headers=headers,
            params=params,
            timeout=_DISCOVERY_TIMEOUT_S,
        )
        response.raise_for_status()
        items = [
            {
                'id': item['id'],
                **({'display_name': item['display_name']} if item.get('display_name') else {}),
            }
            for item in response.json().get('data', [])
            if isinstance(item, dict) and item.get('id')
        ]
        items.sort(key=lambda x: x['id'])
    except Exception as exc:
        logger.warning(
            'model discovery failed for provider %r (%s/models): %s',
            provider.id,
            api_url,
            exc,
        )
        cache.set(cache_key, [], _DISCOVERY_ERROR_TTL_S)
        return []
    cache.set(cache_key, items, _DISCOVERY_TTL_S)
    return items


def _http_discovered_models(provider: Provider, api_key: str | None) -> list[ModelEntry]:
    """Model entries from a gateway's ``/models`` endpoint, enriched from models.dev.

    The display name is the gateway-provided one or the raw model id -- never a
    models.dev name: the gateway may serve a variant that only shares the id.
    """
    entries = []
    for item in _fetch_gateway_models(provider, api_key):
        entry = ModelEntry(id=item['id'], name=item.get('display_name') or item['id'])
        entries.append(enrich_model(entry, provider))
    return entries


def models_from_models_dev(provider_id: str) -> list[ModelEntry]:
    """The full models.dev catalogue for a provider id, or an empty list."""
    md = md_provider(provider_id)
    if md is None:
        return []
    return [_entry_from_md(model_id, model) for model_id, model in sorted(md.models.items())]


def populate_models(provider: Provider, api_key: str | None) -> list[ModelEntry]:
    """Resolve a provider's model list (see the module docstring for priority)."""
    if provider.models is not None:
        return [enrich_model(entry, provider) for entry in provider.models]
    if provider.api_url and provider.type in _DISCOVERABLE_TYPES:
        return _http_discovered_models(provider, api_key)
    models = models_from_models_dev(provider.id)
    if not models:
        logger.warning(
            'provider %r has no models: not a models.dev provider id and no '
            'discoverable api_url (type %r)',
            provider.id,
            provider.type,
        )
    return models
