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
from unittest import IsolatedAsyncioTestCase, TestCase, mock

import django


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bublik.settings')
django.setup()

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
    _split_point,
    _summary_message,
    _transcript,
    estimate_tokens,
)
from bublik.ai.types import ChatDeps, CompactionConfig  # noqa: E402
from bublik.data.models import ChatThread  # noqa: E402
from bublik.data.serializers.chat_thread import ChatThreadDetailSerializer  # noqa: E402


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
        self.deps = ChatDeps(thread_id='t1', user_id=1, run_id='r1')


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
        thread = ChatThread(title='t', context_state={})
        data = ChatThreadDetailSerializer(thread).data
        self.assertIsNone(data['context_usage'])

    def test_state_is_projected(self):
        thread = ChatThread(
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
        data = ChatThreadDetailSerializer(thread).data
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
            },
        )

    def test_compacted_without_tokens_is_not_none(self):
        thread = ChatThread(
            title='t',
            context_state={
                'summary': 'compact-summary',
                'covered_count': 3,
                'compacted_at': '2026-06-01T10:00:00',
            },
        )
        data = ChatThreadDetailSerializer(thread).data
        self.assertIsNotNone(data['context_usage'])
        self.assertEqual(data['context_usage']['tokens'], 0)
        self.assertTrue(data['context_usage']['compacted'])
        self.assertEqual(data['context_usage']['covered_count'], 3)

    def test_client_cannot_write_context_state(self):
        serializer = ChatThreadDetailSerializer(
            data={'title': 't', 'context_usage': {'tokens': 1}, 'context_state': {}},
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn('context_state', serializer.validated_data)
        self.assertNotIn('context_usage', serializer.validated_data)
