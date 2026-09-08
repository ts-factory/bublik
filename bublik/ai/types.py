# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2025-2026 OKTET Labs Ltd. All rights reserved.
"""
Typed view of the ``ai`` global config.

Field names mirror models.dev (https://models.dev): a model has an ``id`` plus a
human-readable ``name`` and the metadata fields ``limit``, ``modalities``,
``tool_call`` and ``reasoning``. Reasoning-effort levels are not configured:
pydantic-ai accepts the same unified levels for every model that supports
thinking, so they are derived from the ``reasoning`` flag.

Save-time validation is done against ``data/schemas/ai.json``. Most models
parse leniently (unknown keys ignored) so runtime does not break on a config
written for a newer schema; providers reject unknown keys so retired secret
fields cannot be silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class AiChatDeps:
    """Per-run context threaded from the chat request into agent tools.

    Passed as the agent's ``deps_type`` (see :func:`bublik.ai.agent.build_agent`)
    and constructed per run in :mod:`bublik.ai.app`; the ``generate_file`` tool
    reads it via ``RunContext[AiChatDeps]``, and the history compactor uses
    ``run_id`` to append its own AG-UI event to the run's Redis buffer (see
    :mod:`bublik.ai.compaction`).
    """

    thread_id: str
    user_id: int
    run_id: str


# The unified reasoning-effort vocabulary of pydantic-ai's
# ``ModelSettings.thinking`` field; every thinking-capable model accepts them.
UNIFIED_THINKING_EFFORTS: tuple[str, ...] = ('minimal', 'low', 'medium', 'high', 'xhigh')
DEFAULT_THINKING_EFFORT = 'medium'
SECRET_REFERENCE_PATTERN = r'^\$\{(?:env|settings):AI_[A-Z0-9_]+\}$'


class _Base(BaseModel):
    model_config = ConfigDict(extra='ignore')


class Limit(_Base):
    """Token limits (mirrors models.dev ``Limit``)."""

    context: int | None = None
    output: int | None = None


class Modalities(_Base):
    """Supported input/output modalities (mirrors models.dev ``Modalities``)."""

    input: list[str] = Field(default_factory=list)
    output: list[str] = Field(default_factory=list)


class ModelEntry(_Base):
    """A model exposed by a provider; unset fields are filled from models.dev."""

    id: str
    name: str | None = None
    limit: Limit | None = None
    modalities: Modalities | None = None
    tool_call: bool | None = None
    reasoning: bool | None = None

    @property
    def reasoning_efforts(self) -> list[str]:
        return list(UNIFIED_THINKING_EFFORTS) if self.reasoning else []

    @property
    def default_reasoning_effort(self) -> str | None:
        return DEFAULT_THINKING_EFFORT if self.reasoning else None


class Provider(_Base):
    """An LLM provider entry; ``models=None`` means auto-populate."""

    model_config = ConfigDict(extra='forbid')

    id: str
    type: str
    name: str | None = None
    api_url: str | None = None
    api_key: str | None = Field(default=None, pattern=SECRET_REFERENCE_PATTERN)
    # Extra HTTP headers sent with every request to this provider. Values may
    # embed ``${env:AI_NAME}``/``${settings:AI_NAME}`` secret references and the
    # per-conversation ``${thread_id}`` placeholder (see
    # :func:`bublik.ai.config.resolve_headers`). Needed by gateways that route
    # on a caller-supplied session id, e.g. OpenCode Go's ``x-opencode-session``.
    # Applied last, over the auth header each SDK derives from ``api_key``, so
    # setting ``Authorization``/``x-api-key`` here deliberately replaces it --
    # the only way to reach a gateway whose auth scheme ``api_key`` (one fixed
    # scheme per provider ``type``) cannot express.
    headers: dict[str, str] = Field(default_factory=dict)
    # Pydantic AI ``ModelSettings`` applied to every request to this provider.
    # Escape hatch for provider quirks the config cannot otherwise reach --
    # most importantly ``openai_continuous_usage_stats`` for gateways that
    # report cumulative usage on every stream chunk (see bublik.ai.compaction).
    model_settings: dict = Field(default_factory=dict)
    models: list[ModelEntry] | None = None


class McpServer(_Base):
    """A remote MCP server exposed to the chat assistant over Streamable HTTP."""

    id: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class DefaultModel(_Base):
    provider: str
    model: str


class CompactionConfig(_Base):
    """Automatic history-compaction settings (see :mod:`bublik.ai.compaction`).

    Compaction is model-side only: the persisted conversation is untouched;
    what is sent to the model gets its older turns replaced by a summary once
    the estimated context occupancy crosses ``threshold`` of the model's
    context window (models without a known window are never compacted).
    """

    enabled: bool = True
    # Fraction of the model's context window that triggers compaction. Only
    # used for models whose output limit is unknown; otherwise the trigger is
    # `context - reserved`, which leaves exactly enough room for the reply.
    threshold: float = 0.8
    # Tokens held back for the model's reply. Defaults to the model's own
    # output limit, capped at `DEFAULT_RESERVED`.
    reserved: int | None = None
    # How many trailing messages are always kept verbatim (the split point
    # may move further back to keep tool call/return pairs intact).
    keep_recent: int = 8


class AiConfig(_Base):
    providers: list[Provider] = Field(default_factory=list)
    default_model: DefaultModel | None = None
    mcp_servers: list[McpServer] = Field(default_factory=list)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
