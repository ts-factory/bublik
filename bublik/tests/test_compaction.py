# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""
Unit tests for automatic history compaction (:mod:`bublik.ai.compaction`).

Pure logic tests: the summarizer runs on pydantic-ai's ``TestModel``, DB and
Redis interactions are patched out, so no live services are needed. Django is
bootstrapped explicitly (this suite runs under plain pytest, without a test
database) -- model instances are used unsaved.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import django


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
django.setup()

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel  # noqa: E402

from bublik.ai import compaction  # noqa: E402
from bublik.ai.compaction import (  # noqa: E402
    Compactor,
    _implausible_reason,
    _split_point,
    _summary_message,
    _transcript,
    context_tokens_from_usage,
    estimate_tokens,
    make_usage_reporter,
)
from bublik.ai.types import AiChatDeps, CompactionConfig  # noqa: E402
from bublik.data.models import AiChatThread  # noqa: E402
from bublik.data.serializers.ai_chat_thread import AiChatThreadDetailSerializer  # noqa: E402


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _tool_call(name: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name=name, args={'x': 1})])


def _tool_return(name: str, content: str = 'ok') -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name=name, content=content, tool_call_id='c1')],
    )


class SplitPointTest(TestCase):
    def test_keeps_recent_and_starts_at_user_request(self):
        # A tool exchange sits inside the tail window; the boundary must move
        # back to the user turn it belongs to, never orphaning the tool return.
        messages = [
            _user('q1'),
            _assistant('a1'),
            _user('q2'),
            _tool_call('list_runs'),
            _tool_return('list_runs'),
            _assistant('a2'),
        ]
        self.assertEqual(_split_point(messages, keep_recent=2), 2)

    def test_nothing_to_compact_when_window_covers_all(self):
        messages = [_user('q1'), _assistant('a1')]
        self.assertEqual(_split_point(messages, keep_recent=8), 0)

    def test_no_user_request_in_reach_means_no_compaction(self):
        # Degenerate history without any user turn before the window start.
        messages = [
            _tool_call('t'),
            _tool_return('t'),
            _assistant('a'),
            _assistant('b'),
        ]
        self.assertEqual(_split_point(messages, keep_recent=2), 0)


class EstimateTokensTest(TestCase):
    def test_counts_text_and_overhead(self):
        messages = [_user('x' * 400), _assistant('y' * 400)]
        estimate = estimate_tokens(messages)
        # 800 chars / 4 = 200 tokens + 2 * overhead.
        self.assertGreaterEqual(estimate, 200)
        self.assertLess(estimate, 250)


class TranscriptTest(TestCase):
    def test_renders_turns_and_truncates_tool_results(self):
        messages = [
            _user('list runs'),
            ModelResponse(
                parts=[
                    ThinkingPart(content='pondering'),
                    ToolCallPart(tool_name='list_runs', args={'day': 'today'}),
                ],
            ),
            _tool_return('list_runs', 'r' * 10000),
            _assistant('found 3 runs'),
        ]
        transcript = _transcript(messages)
        self.assertIn('User: list runs', transcript)
        self.assertIn('list_runs', transcript)
        self.assertIn('Assistant: found 3 runs', transcript)
        self.assertNotIn('pondering', transcript)  # thinking is dropped
        self.assertIn('chars truncated', transcript)  # tool result capped


class _Ctx:
    """Minimal RunContext stand-in: the compactor only reads ``deps``."""

    def __init__(self):
        self.deps = AiChatDeps(thread_id='t1', user_id=1, run_id='r1')


def _compactor(**overrides) -> Compactor:
    defaults = {
        'config': CompactionConfig(threshold=0.8, keep_recent=2),
        'context_limit': 1000,
        'summarizer_model': TestModel(custom_output_text='the summary'),
    }
    defaults.update(overrides)
    return Compactor(**defaults)


class CompactorTest(IsolatedAsyncioTestCase):
    def setUp(self):
        # No DB / Redis in unit tests: state is injected, writes recorded.
        self.state_writes = []

        async def fake_write(thread_id, updates):
            self.state_writes.append((thread_id, updates))

        self.write_patch = mock.patch.object(
            compaction,
            'sync_to_async',
            lambda fn: {
                compaction._read_state: mock.AsyncMock(return_value={}),
                compaction._write_state: fake_write,
            }[fn],
        )
        self.event_patch = mock.patch.object(
            compaction.run_store,
            'append_event',
            mock.AsyncMock(),
        )
        self.write_patch.start()
        self.append_event = self.event_patch.start()

    def tearDown(self):
        self.write_patch.stop()
        self.event_patch.stop()

    async def test_inert_without_context_limit(self):
        compactor = _compactor(context_limit=None)
        messages = [_user('q')]
        self.assertIs(await compactor(_Ctx(), messages), messages)

    async def test_under_threshold_passes_through(self):
        compactor = _compactor()
        compactor._state = {'context_tokens': 100}  # 100 < 0.8 * 1000
        messages = [_user('q1'), _assistant('a1'), _user('q2'), _assistant('a2')]
        result = await compactor(_Ctx(), messages)
        self.assertEqual(result, messages)

    async def test_over_threshold_compacts_and_emits_event(self):
        compactor = _compactor()
        compactor._state = {'context_tokens': 900}  # 900 > 0.8 * 1000
        messages = [_user('q1'), _assistant('a1'), _user('q2'), _assistant('a2')]
        result = await compactor(_Ctx(), messages)

        # [summary, q2, a2]: split lands on the q2 user turn.
        self.assertEqual(len(result), 3)
        self.assertIn('the summary', result[0].parts[0].content)
        self.assertEqual(result[1:], messages[2:])
        # Cache persisted with the original-history message count covered.
        self.assertEqual(len(self.state_writes), 1)
        _thread, updates = self.state_writes[0]
        self.assertEqual(updates['covered_count'], 2)
        self.assertEqual(updates['summary'], 'the summary')
        # The compaction event was appended to the run's buffer.
        self.append_event.assert_awaited_once()
        self.assertIn(compaction.COMPACTED_EVENT, self.append_event.await_args.args[1])

    async def _run_compaction_capturing_settings(self, compactor):
        """Drive one real compaction, returning the summarizer's model_settings."""
        captured = {}
        real_run = Agent.run

        async def spy(agent_self, *args, **kwargs):
            captured['model_settings'] = kwargs.get('model_settings')
            return await real_run(agent_self, *args, **kwargs)

        compactor._state = {'context_tokens': 900}
        messages = [_user('q1'), _assistant('a1'), _user('q2'), _assistant('a2')]
        with mock.patch.object(Agent, 'run', spy):
            await compactor(_Ctx(), messages)
        return captured['model_settings']

    async def test_summarizer_receives_the_runs_model_settings(self):
        # The summarizer is a second agent hitting the same provider endpoint:
        # a gateway requiring a session header (OpenCode Go) would fail
        # compaction while ordinary chat kept working.
        settings = {'extra_headers': {'x-opencode-session': 't1'}}
        compactor = _compactor(model_settings=settings)
        self.assertEqual(await self._run_compaction_capturing_settings(compactor), settings)

    async def test_summarizer_settings_default_to_none(self):
        self.assertIsNone(await self._run_compaction_capturing_settings(_compactor()))

    async def test_compacts_once_per_run(self):
        compactor = _compactor()
        compactor._state = {'context_tokens': 900}
        messages = [_user('q1'), _assistant('a1'), _user('q2'), _assistant('a2')]
        first = await compactor(_Ctx(), messages)
        # Next model request of the same run: cached summary applied, no new
        # summarization (no extra write/event). Summary messages are compared
        # by content: each construction stamps a fresh part timestamp.
        second = await compactor(_Ctx(), messages)
        self.assertEqual(second[0].parts[0].content, first[0].parts[0].content)
        self.assertEqual(second[1:], first[1:])
        self.assertEqual(len(self.state_writes), 1)
        self.append_event.assert_awaited_once()

    async def test_cached_summary_is_applied_cheaply(self):
        compactor = _compactor()
        compactor._state = {
            'context_tokens': 100,  # under threshold: no fresh compaction
            'summary': 'older stuff',
            'covered_count': 2,
        }
        messages = [_user('q1'), _assistant('a1'), _user('q2'), _assistant('a2')]
        result = await compactor(_Ctx(), messages)
        self.assertEqual(len(result), 3)
        self.assertIn('older stuff', result[0].parts[0].content)
        self.assertEqual(result[1:], messages[2:])
        self.assertEqual(self.state_writes, [])

    async def test_stale_cache_is_discarded_when_history_truncated(self):
        compactor = _compactor()
        compactor._state = {
            'context_tokens': 100,
            'summary': 'older stuff',
            'covered_count': 10,  # covers more than the client resent
        }
        messages = [_user('q1'), _assistant('a1')]
        result = await compactor(_Ctx(), messages)
        self.assertEqual(result, messages)

    async def test_failures_degrade_to_uncompacted(self):
        compactor = _compactor()
        compactor._state = {'context_tokens': 900}
        messages = [_user('q1'), _assistant('a1'), _user('q2'), _assistant('a2')]
        with mock.patch.object(
            compaction,
            '_transcript',
            side_effect=RuntimeError('boom'),
        ):
            result = await compactor(_Ctx(), messages)
        self.assertEqual(result, messages)


class SummaryMessageTest(TestCase):
    def test_is_a_user_request(self):
        message = _summary_message('s')
        self.assertIsInstance(message, ModelRequest)
        self.assertIsInstance(message.parts[0], UserPromptPart)


class ContextUsageSerializerTest(TestCase):
    """The thread detail serializer derives ``context_usage`` (read-only)."""

    def test_absent_state_serializes_to_none(self):
        thread = AiChatThread(title='t', context_state={})
        data = AiChatThreadDetailSerializer(thread).data
        self.assertIsNone(data['context_usage'])

    def test_state_is_projected(self):
        thread = AiChatThread(
            title='t',
            context_state={
                'context_tokens': 1234,
                'context_limit': 128000,
                'provider': 'anthropic',
                'model': 'claude',
                'summary': 's',
                'covered_count': 5,
                'compacted_at': '2026-07-01T12:00:00',
            },
        )
        data = AiChatThreadDetailSerializer(thread).data
        self.assertEqual(
            data['context_usage'],
            {
                'tokens': 1234,
                'context_limit': 128000,
                'provider': 'anthropic',
                'model': 'claude',
                'compacted': True,
                'covered_count': 5,
                'compacted_at': '2026-07-01T12:00:00',
                'raw_usage': None,
            },
        )

    def test_compacted_without_tokens_is_not_none(self):
        thread = AiChatThread(
            title='t',
            context_state={
                'summary': 'compact-summary',
                'covered_count': 3,
                'compacted_at': '2026-06-01T10:00:00',
            },
        )
        data = AiChatThreadDetailSerializer(thread).data
        self.assertIsNotNone(data['context_usage'])
        self.assertEqual(data['context_usage']['tokens'], 0)
        self.assertTrue(data['context_usage']['compacted'])
        self.assertEqual(data['context_usage']['covered_count'], 3)

    def test_client_cannot_write_context_state(self):
        serializer = AiChatThreadDetailSerializer(
            data={'title': 't', 'context_usage': {'tokens': 1}, 'context_state': {}},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('context_state', serializer.validated_data)
        self.assertNotIn('context_usage', serializer.validated_data)


def _usage(**kwargs):
    fields = {
        'input_tokens': 0,
        'output_tokens': 0,
        'cache_read_tokens': 0,
        'cache_write_tokens': 0,
    }
    fields.update(kwargs)
    return SimpleNamespace(**fields)


class ContextTokensFromUsageTest(TestCase):
    """Cache buckets are nested inside `input_tokens` for some providers only."""

    def test_openai_flavored_cache_reads_are_already_inside_input(self):
        # prompt_tokens=5000 of which 4000 cached: the request occupied 5000+50,
        # not 9050. Adding the cache bucket would roughly double every cached
        # request and push a short thread over the compaction threshold.
        usage = _usage(input_tokens=5000, cache_read_tokens=4000, output_tokens=50)
        self.assertEqual(context_tokens_from_usage(usage, 'openai'), 5050)

    def test_anthropic_cache_buckets_are_additive(self):
        # Anthropic's input_tokens excludes both cache buckets.
        usage = _usage(
            input_tokens=100,
            cache_read_tokens=5000,
            cache_write_tokens=200,
            output_tokens=50,
        )
        self.assertEqual(context_tokens_from_usage(usage, 'anthropic'), 5350)

    def test_gateway_prefixed_anthropic_is_recognized(self):
        usage = _usage(input_tokens=100, cache_read_tokens=900, output_tokens=0)
        self.assertEqual(context_tokens_from_usage(usage, 'gateway/anthropic'), 1000)

    def test_unknown_provider_type_defaults_to_input_plus_output(self):
        usage = _usage(input_tokens=10, cache_read_tokens=999, output_tokens=5)
        self.assertEqual(context_tokens_from_usage(usage, ''), 15)


class UsageReporterTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.writes = []

        async def fake_write(thread_id, updates):
            self.writes.append((thread_id, updates))

        self.patch = mock.patch.object(
            compaction,
            'sync_to_async',
            lambda fn: fake_write if fn is compaction._write_state else fn,
        )
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    async def _report(self, usage, *, context_limit, provider_type='openai', history=()):
        response = ModelResponse(parts=[TextPart(content='hi')])
        response.usage = usage
        messages = [*history, response]
        result = SimpleNamespace(all_messages=lambda: messages)
        reporter = make_usage_reporter(
            't1', 'opencode-go', 'deepseek-v4-flash', context_limit, provider_type
        )
        return [event async for event in reporter(result)]

    async def test_persists_plausible_usage(self):
        events = await self._report(
            _usage(input_tokens=9000, cache_read_tokens=8000, output_tokens=50),
            context_limit=1_000_000,
        )
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.writes[0][1]['context_tokens'], 9050)
        self.assertEqual(len(events), 1)

    async def test_ignores_usage_above_the_context_window(self):
        # A gateway reporting session-cumulative cache figures would otherwise
        # pin the thread over the threshold and compact on every single turn.
        events = await self._report(
            _usage(input_tokens=3_939_310, output_tokens=0),
            context_limit=1_000_000,
        )
        self.assertEqual(self.writes, [])
        self.assertEqual(events, [])

    async def test_persists_the_providers_raw_breakdown(self):
        await self._report(
            _usage(input_tokens=1200, cache_read_tokens=800, output_tokens=40),
            context_limit=1_000_000,
        )
        native = self.writes[0][1]['raw_usage']
        # Kept verbatim so a gateway reporting nonsense can be diagnosed from
        # the stored thread rather than from a live repro.
        self.assertEqual(native['input_tokens'], 1200)
        self.assertEqual(native['cache_read_tokens'], 800)
        self.assertEqual(native['output_tokens'], 40)

    async def test_ignores_usage_far_above_the_history_estimate(self):
        # The real OpenCode Go failure: ~1.5K of conversation reported as
        # 952,086 tokens, which slips under a 1M context window and so has to be
        # caught by the ratio check instead.
        history = [_user('x' * 6000)]
        events = await self._report(
            _usage(input_tokens=952_086, output_tokens=0),
            context_limit=1_000_000,
            history=history,
        )
        self.assertEqual(self.writes, [])
        self.assertEqual(events, [])

    async def test_keeps_usage_a_few_times_the_estimate(self):
        # The estimate sees neither the system prompt nor the tool schemas, so
        # being several times larger is normal and must not be rejected.
        history = [_user('x' * 240_000)]
        await self._report(
            _usage(input_tokens=200_000, output_tokens=500),
            context_limit=1_000_000,
            history=history,
        )
        self.assertEqual(self.writes[0][1]['context_tokens'], 200_500)

    async def test_small_totals_are_never_rejected_by_the_ratio(self):
        # Below the floor the ratio is too noisy to judge: a 40-token history
        # against 9K of real context is a 225x ratio and perfectly normal.
        await self._report(
            _usage(input_tokens=9000, output_tokens=50),
            context_limit=1_000_000,
            history=[_user('hello')],
        )
        self.assertEqual(self.writes[0][1]['context_tokens'], 9050)

    async def test_without_a_known_window_the_value_is_kept(self):
        await self._report(_usage(input_tokens=5000, output_tokens=10), context_limit=None)
        self.assertEqual(self.writes[0][1]['context_tokens'], 5010)


class UsableContextTest(TestCase):
    """Compaction triggers at `context - reserved`, opencode's usable()."""

    def _usable(self, *, context, output=None, reserved=None, threshold=0.8):
        return _compactor(
            config=CompactionConfig(threshold=threshold, reserved=reserved),
            context_limit=context,
            output_limit=output,
        )._usable()

    def test_reserves_the_models_output_limit(self):
        # A 1M window with a 64K reply cap reserves DEFAULT_RESERVED (20K), so
        # 980K stays usable; the old flat 80% threw away 180K of real context.
        self.assertEqual(self._usable(context=1_000_000, output=64_000), 980_000)

    def test_reserve_is_capped(self):
        self.assertEqual(self._usable(context=200_000, output=8_000), 192_000)

    def test_falls_back_to_the_threshold_fraction_without_an_output_limit(self):
        self.assertEqual(self._usable(context=200_000, output=None), 160_000)

    def test_explicit_reserved_wins(self):
        self.assertEqual(self._usable(context=200_000, output=8_000, reserved=50_000), 150_000)

    def test_reserved_larger_than_the_window_clamps_to_zero(self):
        self.assertEqual(self._usable(context=10_000, reserved=50_000), 0)


class ImplausibleReasonTest(TestCase):
    def test_above_the_context_window(self):
        self.assertIn('context window', _implausible_reason(3_939_310, 1_000_000, 10_000))

    def test_far_above_the_history_estimate(self):
        self.assertIn('estimate', _implausible_reason(952_086, 1_000_000, 1_500))

    def test_plausible_values_pass(self):
        self.assertIsNone(_implausible_reason(900_000, 1_000_000, 800_000))
        self.assertIsNone(_implausible_reason(9_000, 1_000_000, 1_500))

    def test_no_known_window_still_uses_the_estimate(self):
        self.assertIsNone(_implausible_reason(9_000, None, 100))
        self.assertIsNotNone(_implausible_reason(952_086, None, 1_500))
