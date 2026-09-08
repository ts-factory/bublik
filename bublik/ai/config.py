# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Loading and shaping of the AI chat configuration.

Providers are defined in the active ``ai`` global config (validated against
``data/schemas/ai.json`` at save time and parsed into the typed models of
:mod:`bublik.ai.types` here). API keys are resolved from explicit
``${env:AI_NAME}`` or ``${settings:AI_NAME}`` references (keys are never stored
in the DB), model lists are populated/enriched via
:mod:`bublik.ai.discovery`, and :func:`public_models` produces the sanitized,
UI-facing view.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid

from django.conf import settings
from pydantic import ValidationError

from bublik.ai.discovery import populate_models
from bublik.ai.types import AiConfig, ModelEntry, Provider
from bublik.core.config.services import ConfigServices
from bublik.data.models import GlobalConfigs


logger = logging.getLogger(__name__)


def get_raw_ai_config(project_id: int | None = None) -> dict:
    """Return the active ``ai`` config content as authored, or an empty dict."""
    content = ConfigServices.get_global_content_from_cache(
        GlobalConfigs.AI.name,
        project_id,
    )
    return content or {}


def parse_ai_config(raw: dict) -> AiConfig:
    """Parse authored config content into its typed form.

    A stored config that no longer matches the current shape (e.g. written
    against an older schema) degrades to an empty provider set with an error
    logged, instead of breaking every /chat endpoint.
    """
    try:
        return AiConfig.model_validate(raw)
    except ValidationError as exc:
        logger.error('active ai config does not match the current schema: %s', exc)
        return AiConfig()


def get_ai_config(project_id: int | None = None) -> AiConfig:
    """Return the active ``ai`` config parsed into its typed form."""
    return parse_ai_config(get_raw_ai_config(project_id))


def config_fingerprint(raw_config: dict) -> str:
    """Stable digest of the authored config content, used to key the agent cache.

    Editing the ``ai`` config (or rotating a key reference) changes the
    fingerprint, so cached agents built from the old content are not reused.
    """
    return hashlib.sha1(
        json.dumps(raw_config, sort_keys=True).encode(),
    ).hexdigest()


def find_provider(config: AiConfig, provider_id: str) -> Provider | None:
    """Return the provider with the given id, or ``None``."""
    return next((p for p in config.providers if p.id == provider_id), None)


def find_model(provider: Provider, model_id: str) -> ModelEntry | None:
    """Return the provider's model with the given id, or ``None``."""
    return next((m for m in provider.models or [] if m.id == model_id), None)


class ModelRequestError(ValueError):
    """A provider/model/effort selection could not be resolved from the config.

    A ``ValueError`` subclass so existing ``except ValueError`` handlers (and the
    route's 422 mapping) keep working while callers that care can catch this
    specific case.
    """


def resolve_model_request(
    config: AiConfig,
    provider_id: str,
    model_id: str,
    effort: str | None = None,
) -> tuple[Provider, ModelEntry, str | None]:
    """Resolve and validate a provider/model/effort selection against ``config``.

    Returns the matched provider, the matched model entry, and the effort
    normalized to the model's default when none was requested. Raises
    :class:`ModelRequestError` (a ``ValueError``) with an actionable message when
    the provider or model is unknown, or the requested effort is unsupported.

    The single validation path shared by the chat route (mapping the error to a
    422) and :func:`bublik.ai.agent.build_agent`, so both agree on what a valid
    request is and on the messages users see.
    """
    provider = find_provider(config, provider_id)
    if provider is None:
        msg = f'Unknown chat provider: {provider_id!r}'
        raise ModelRequestError(msg)
    model = find_model(provider, model_id)
    if model is None:
        msg = f'Unknown model {model_id!r} for provider {provider_id!r}'
        raise ModelRequestError(msg)
    if effort and effort not in model.reasoning_efforts:
        msg = f'Unsupported reasoning effort {effort!r} for model {model_id!r}.'
        raise ModelRequestError(msg)
    return provider, model, effort or model.default_reasoning_effort


# Only source-qualified, AI_-prefixed names may be dereferenced as secrets.
# Without this, a config author could point an API key or MCP header at any
# Django setting (SECRET_KEY, database credentials, ...) and send it to an
# api_url / MCP server they also control.
_SECRET_REFERENCE_RE = re.compile(
    r'^\$\{(?P<source>env|settings):(?P<name>AI_[A-Z0-9_]+)\}$',
)


def resolve_secret_reference(reference: str | None) -> str | None:
    """Resolve one strict, source-qualified ``AI_`` secret reference.

    ``env`` reads only :data:`os.environ`; ``settings`` reads only
    :mod:`django.conf.settings`. Invalid references and missing, empty, or
    non-string values are unresolved and return ``None``.
    """
    if not reference or (match := _SECRET_REFERENCE_RE.fullmatch(reference)) is None:
        return None
    name = match.group('name')
    if match.group('source') == 'env':
        value = os.environ.get(name)
    else:
        value = getattr(settings, name, None)
    return value if isinstance(value, str) and value else None


def resolve_api_key(provider: Provider) -> str | None:
    """Resolve a provider's optional, source-qualified API key reference."""
    return resolve_secret_reference(provider.api_key)


# Every complete ${...} placeholder. Whether its contents are a valid
# substitution or secret reference is decided per match below; static text
# passes through untouched.
_PLACEHOLDER_RE = re.compile(r'\$\{[^}]*\}')


def resolve_headers(
    headers: dict[str, str],
    label: str,
    substitutions: dict[str, str] | None = None,
) -> dict[str, str] | None:
    """Resolve every placeholder in a header mapping, or ``None`` on failure.

    ``substitutions`` supplies non-secret values by bare name (``${thread_id}``)
    and is consulted first; anything else must be a strict source-qualified
    ``AI_`` secret reference (see :func:`resolve_secret_reference`).

    All-or-nothing by design: a placeholder with invalid syntax, a non-``AI_``
    name, or an unresolved value makes the whole mapping unusable, so the caller
    drops it rather than sending a literal ``${...}`` -- or a header the remote
    end routes on -- upstream. ``label`` only names the source in the log line.
    """
    substitutions = substitutions or {}
    resolved: dict[str, str] = {}
    for key, value in headers.items():
        unresolved: list[str] = []

        def _sub(match: re.Match, _unresolved=unresolved) -> str:
            placeholder = match.group(0)
            name = placeholder[2:-1]
            if name in substitutions:
                return substitutions[name]
            secret = resolve_secret_reference(placeholder)
            if secret is None:
                _unresolved.append(placeholder)
                return ''
            return secret

        substituted = _PLACEHOLDER_RE.sub(_sub, value)
        if unresolved:
            logger.warning(
                '%s: headers dropped, unresolved reference(s) %s in header %r',
                label,
                ', '.join(unresolved),
                key,
            )
            return None
        # A newline in a resolved value would let a poisoned env var smuggle in
        # extra headers; refuse rather than sanitize.
        if '\r' in substituted or '\n' in substituted:
            logger.warning(
                '%s: headers dropped, header %r resolved to a value containing a newline',
                label,
                key,
            )
            return None
        resolved[key] = substituted
    return resolved


def resolve_provider_headers(
    provider: Provider,
    thread_id: str | None = None,
) -> dict[str, str]:
    """A provider's request headers, resolved for one conversation.

    ``${thread_id}`` becomes ``thread_id``: gateways such as OpenCode Go route
    and cache on a caller-supplied session id that must stay stable for the
    whole conversation, which is exactly what a Bublik thread id is. Callers
    without a conversation (model discovery) pass a stable synthetic id.
    Returns an empty mapping when nothing is configured or resolution failed.
    """
    if not provider.headers:
        return {}
    substitutions = {'thread_id': thread_id} if thread_id else {}
    return resolve_headers(provider.headers, f'provider {provider.id!r}', substitutions) or {}


# Model discovery has no conversation to key on, but a gateway may still require
# the session header on its /models endpoint. A namespaced UUID5 is stable across
# restarts and shaped like the per-conversation ids, without impersonating one.
DISCOVERY_SESSION_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, 'bublik-model-discovery'))


def effective_ai_config(config: AiConfig) -> AiConfig:
    """The config with every provider's model list populated and enriched.

    Returns a copy; the authored content (whose fingerprint keys the agent
    cache) is left untouched.
    """
    providers = [
        provider.model_copy(
            update={
                'models': populate_models(
                    provider,
                    resolve_api_key(provider),
                    resolve_provider_headers(provider, DISCOVERY_SESSION_ID),
                ),
            },
        )
        for provider in config.providers
    ]
    return config.model_copy(update={'providers': providers})


def get_effective_ai_config(project_id: int | None = None) -> AiConfig:
    """Load the active ``ai`` config and resolve model lists."""
    return effective_ai_config(get_ai_config(project_id))


def public_models(config: AiConfig) -> dict:
    """
    Build the UI-facing model list.

    Strips everything sensitive or backend-only (``api_url``, ``api_key``):
    the chat UI only needs identifiers, labels, model metadata and the derived
    reasoning-effort options.
    """
    providers = []
    for provider in config.providers:
        models = []
        for model in provider.models or []:
            models.append(
                {
                    'id': model.id,
                    'name': model.name or model.id,
                    'limit': model.limit.model_dump() if model.limit else None,
                    'modalities': (model.modalities.model_dump() if model.modalities else None),
                    'tool_call': model.tool_call,
                    'reasoning': model.reasoning,
                    # Derived for the UI: a non-empty effort list means support.
                    'supports_reasoning_effort': bool(model.reasoning_efforts),
                    'reasoning_efforts': model.reasoning_efforts,
                    'default_reasoning_effort': model.default_reasoning_effort,
                },
            )
        providers.append(
            {
                'id': provider.id,
                'type': provider.type,
                'name': provider.name or provider.id,
                'models': models,
            },
        )
    return {
        'providers': providers,
        'default_model': (config.default_model.model_dump() if config.default_model else None),
    }
