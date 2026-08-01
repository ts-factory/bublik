# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Pydantic AI agent factory for the Bublik chat assistant.

Providers and models are defined in the ``ai`` global config (see
``bublik.ai.config``). A single agent definition is reused across them; only
the model adapter changes. Model dispatch is delegated to
:func:`pydantic_ai.models.infer_model`, so any provider pydantic-ai supports
(openai, anthropic, google, groq, deepseek, alibaba, moonshotai, ...) can be
configured -- subject to that provider's optional package being installed.
Custom OpenAI-protocol gateways use ``type: "openai"`` with an ``api_url``;
gateways that speak the Anthropic Messages API use the native ``anthropic``
type with an ``api_url``. The agent's tools are the shared Bublik tools
(``bublik.mcp.tools.MCP_TOOLS``) called in-process, so the chat assistant has
access to exactly the same tools the Bublik MCP server exposes -- without any
network hop to a separate MCP server.
"""

from __future__ import annotations

from functools import lru_cache, wraps
import inspect
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry, UserError
from pydantic_ai.models import infer_model
from pydantic_ai.providers import infer_provider, infer_provider_class
from pydantic_ai.providers.gateway import gateway_provider
from rest_framework.exceptions import ValidationError as DRFValidationError

from bublik.ai.config import (
    get_effective_ai_config,
    resolve_api_key,
    resolve_model_request,
)
from bublik.ai.files import generate_file
from bublik.ai.mcp import build_mcp_toolsets
from bublik.ai.prompts import build_system_instructions
from bublik.ai.types import ChatDeps
from bublik.mcp.tools import MCP_TOOLS


if TYPE_CHECKING:
    from pydantic_ai.settings import ModelSettings

    from bublik.ai.types import Provider


# Bublik ``type`` aliases mapped to a pydantic-ai model kind. ``openai-chat``
# forces ``OpenAIChatModel`` (bare ``openai`` in pydantic-ai selects the
# Responses API, which we do not want, and it is the protocol every custom
# OpenAI-compatible gateway speaks). Any other ``type`` is passed to
# pydantic-ai's own provider/model inference unchanged.
TYPE_ALIASES = {
    'openai': 'openai-chat',
}


def _extra_hint(provider_kind: str, exc: Exception) -> str:
    """Best-effort ``pip install`` hint for a missing optional provider package."""
    # pydantic-ai's own ImportError already names the extra; surface it verbatim
    # when present, otherwise fall back to the provider kind as the extra name.
    detail = str(exc).strip()
    if detail:
        return detail
    return (
        f'Provider type {provider_kind!r} requires an optional package: '
        f'pip install "pydantic-ai-slim[{provider_kind}]"'
    )


def _make_provider_factory(provider: Provider, api_key: str | None):
    """Build a ``provider_factory`` for ``infer_model`` that injects our credentials.

    Provider constructors are not uniform (some take ``base_url``, some ``api_base``,
    some only ``api_key``), so we introspect each class ``__init__`` and pass only the
    kwargs it accepts. When we have no overrides to inject we defer to pydantic-ai's
    env-var-based ``infer_provider`` so standard keys (``OPENAI_API_KEY`` etc.) work.

    ``gateway/<upstream>`` types go through :func:`gateway_provider` instead:
    building the upstream provider class directly would skip the gateway's route
    path and auth request hook.
    """
    api_url = provider.api_url

    def factory(provider_kind: str):
        if provider_kind.startswith('gateway/'):
            return gateway_provider(
                provider_kind.removeprefix('gateway/'),
                api_key=api_key,
                base_url=api_url or None,
            )
        cls = infer_provider_class(provider_kind)
        params = inspect.signature(cls.__init__).parameters
        kwargs = {}
        if api_key and 'api_key' in params:
            kwargs['api_key'] = api_key
        if api_url:
            if 'base_url' in params:
                kwargs['base_url'] = api_url
            elif 'api_base' in params:
                kwargs['api_base'] = api_url
        if not kwargs:
            return infer_provider(provider_kind)
        return cls(**kwargs)

    return factory


def _flatten_detail(detail: object) -> str:
    """Flatten a DRF ``ValidationError.detail`` into a plain string.

    ``detail`` may be an ``ErrorDetail``, a list of them, or a nested dict; the
    default ``str()`` yields the noisy ``[ErrorDetail(string='...', code=...)]``
    repr, so recurse into it and join the leaf messages instead.
    """
    if isinstance(detail, list):
        return '; '.join(_flatten_detail(item) for item in detail)
    if isinstance(detail, dict):
        return '; '.join(f'{key}: {_flatten_detail(value)}' for key, value in detail.items())
    return str(detail)


def _as_retry_tool(fn):
    """Wrap a shared MCP tool so DRF ``ValidationError`` becomes a ``ModelRetry``.

    The shared tools raise ``rest_framework`` ``ValidationError`` for bad input
    (e.g. a package Result ID passed to ``get_run_leaf_results``). Surfaced
    verbatim, pydantic-ai leaks its ``[ErrorDetail(...)]`` repr and the model
    does not reliably self-correct. Re-raising as ``ModelRetry`` feeds a clean,
    actionable message back to the model as a retry prompt (bounded by the
    agent's ``retries``). ``functools.wraps`` preserves the signature/docstring
    pydantic-ai introspects, so the tool schema is unchanged.
    """

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except DRFValidationError as exc:
            raise ModelRetry(_flatten_detail(exc.detail)) from exc

    return wrapper


def _infer_provider_model(provider: Provider, provider_id: str, model_id: str):
    """Build the pydantic-ai model for a resolved provider entry.

    The shared credential/inference path of :func:`build_agent` and
    :func:`build_model`: injects the configured api key/url via a custom
    ``provider_factory`` and maps SDK construction failures to config-level
    ``ValueError``s.
    """
    api_key = resolve_api_key(provider)
    if provider.api_key and api_key is None:
        # Without this, pydantic-ai silently sends an 'api-key-not-set'
        # placeholder to a custom base_url and the gateway's 401 is all the
        # user ever sees. Fail here with the actual cause instead.
        msg = (
            f'API key reference {provider.api_key!r} is unresolved for provider {provider_id!r}'
        )
        raise ValueError(msg)
    provider_kind = TYPE_ALIASES.get(provider.type, provider.type)

    factory = _make_provider_factory(provider, api_key)
    try:
        return infer_model(f'{provider_kind}:{model_id}', provider_factory=factory)
    except ImportError as exc:
        raise ValueError(_extra_hint(provider_kind, exc)) from exc
    except UserError as exc:
        msg = f'Unsupported provider type {provider.type!r}: {exc}'
        raise ValueError(msg) from exc
    except Exception as exc:
        # Provider construction is entirely config-driven; SDK init errors are
        # config problems the caller should see as such, not opaque 500s.
        msg = f'Failed to initialize provider {provider_id!r}: {exc}'
        raise ValueError(msg) from exc


def build_model(provider_id: str, model_id: str):
    """Build a bare pydantic-ai model for the given provider/model selection.

    Same config/credential path as :func:`build_agent`, without the agent
    wrapping -- used by :mod:`bublik.ai.compaction` for its tool-less
    summarizer. Synchronous (reads the config through the Django cache), so
    async callers wrap it in ``sync_to_async``.
    """
    config = get_effective_ai_config()
    provider, _model_entry, _ = resolve_model_request(config, provider_id, model_id)
    return _infer_provider_model(provider, provider_id, model_id)


@lru_cache(maxsize=64)
def build_agent(
    provider_id: str,
    model_id: str,
    reasoning_effort: str | None = None,
    config_fingerprint: str | None = None,
) -> Agent:
    """
    Build (and cache) the Bublik chat agent for the given provider and model.

    The provider entry (api url, key reference, type) is resolved from the active
    ``ai`` config. Dispatch is delegated to :func:`pydantic_ai.models.infer_model`, so
    any provider pydantic-ai supports can be configured; the provider's credentials
    are injected via a custom ``provider_factory``. Cached per ``(provider_id,
    model_id, reasoning_effort, config_fingerprint)``: the fingerprint (see
    :func:`bublik.ai.config.config_fingerprint`) ties each cached agent to the config
    content it was built from, so config edits take effect without a restart.
    """
    config = get_effective_ai_config()
    # Same resolver the route validates with, so an unknown provider/model or an
    # unsupported effort fails here with the identical message rather than an
    # opaque error deeper in provider construction.
    provider, model_entry, _ = resolve_model_request(
        config, provider_id, model_id, reasoning_effort
    )

    model = _infer_provider_model(provider, provider_id, model_id)

    # pydantic-ai's unified ``thinking`` field works across providers; each
    # model class translates it to its provider's reasoning parameter and
    # gates it by the model's own profile.
    model_settings: ModelSettings = {}
    if model_entry.limit and model_entry.limit.output:
        model_settings['max_tokens'] = model_entry.limit.output
    if reasoning_effort and reasoning_effort in model_entry.reasoning_efforts:
        model_settings['thinking'] = reasoning_effort

    return Agent(
        model,
        instructions=build_system_instructions(),
        deps_type=ChatDeps,
        # generate_file is chat-only: it needs the run's thread/user deps,
        # which the MCP server does not have -- keep it out of MCP_TOOLS. The
        # shared tools are wrapped so their DRF validation errors reach the
        # model as clean, correctable ModelRetry prompts instead of a raw
        # error repr.
        tools=[*(_as_retry_tool(tool) for tool in MCP_TOOLS), generate_file],
        # Remote MCP servers from the config contribute their own tools. Their
        # connections are opened per run via ``async with agent:`` in the
        # app's background run task (see bublik.ai.app._produce_run).
        toolsets=build_mcp_toolsets(config),
        retries=5,
        model_settings=model_settings or None,
    )
