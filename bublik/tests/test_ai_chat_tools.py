# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Which tools a chat session gets, and how their refusals reach the model."""

import asyncio
from unittest import IsolatedAsyncioTestCase

from django.test import SimpleTestCase, override_settings
from fastmcp.exceptions import ToolError
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.models.test import TestModel

from bublik.ai.agent import _as_retry_tool, chat_tools
from bublik.ai.prompts import build_system_instructions
from bublik.core.auth import _acting_user_id, bind_acting_user
from bublik.core.exceptions import NotFoundError
from bublik.mcp import tools
from bublik.mcp.auth import NOT_ADMIN


def _names(callables):
    return {tool.__name__ for tool in callables}


class ChatToolsTest(SimpleTestCase):
    def test_everyone_gets_reads_writes_and_file_generation(self):
        names = _names(chat_tools(admin=False))

        assert set(tools.MCP_WRITE_TOOL_NAMES) <= names
        assert _names(tools.MCP_TOOLS) <= names
        assert 'generate_file' in names

    def test_admin_tools_are_absent_for_a_plain_user(self):
        names = _names(chat_tools(admin=False))

        assert not set(tools.MCP_ADMIN_TOOL_NAMES) & names

    def test_admin_tools_are_present_for_an_administrator(self):
        names = _names(chat_tools(admin=True))

        assert set(tools.MCP_ADMIN_TOOL_NAMES) <= names

    def test_wrapped_tools_keep_their_schema_signature(self):
        by_name = {tool.__name__: tool for tool in chat_tools(admin=True)}
        assert by_name['set_run_compromised'].__doc__ == tools.set_run_compromised.__doc__
        assert by_name['edit_config'].__wrapped__ is tools.edit_config


class RetryToolTest(IsolatedAsyncioTestCase):
    async def test_a_refusal_becomes_a_model_retry_with_the_same_message(self):
        async def refused():
            raise ToolError(NOT_ADMIN)

        with self.assertRaises(ModelRetry) as caught:
            await _as_retry_tool(refused)()

        assert 'restricted to Bublik administrators' in str(caught.exception)

    async def test_an_unknown_id_becomes_a_model_retry(self):
        async def missing():
            msg = 'Comment 5 not found'
            raise NotFoundError(msg)

        with self.assertRaises(ModelRetry) as caught:
            await _as_retry_tool(missing)()

        assert 'Comment 5 not found' in str(caught.exception)


class ActingUserPropagationTest(IsolatedAsyncioTestCase):
    async def test_the_binding_reaches_a_tool_the_agent_runs(self):
        # TestModel calls every registered tool once, inside the spawned task.
        seen = []

        async def who_is_calling() -> str:
            """Report who the tool is running as."""
            seen.append(_acting_user_id.get())
            return 'ok'

        agent = Agent(TestModel(), tools=[who_is_calling])
        with bind_acting_user(42):
            task = asyncio.ensure_future(agent.run('hi'))
        await task

        assert seen == [42]
        assert _acting_user_id.get() is None


class SystemInstructionsTest(SimpleTestCase):
    @override_settings(BUBLIK_FQDN='')
    def test_the_write_guide_is_always_present(self):
        text = build_system_instructions()

        assert 'set_run_compromised' in text
        assert 'Editing Configs' not in text

    @override_settings(BUBLIK_FQDN='')
    def test_the_admin_guide_is_added_for_an_administrator(self):
        text = build_system_instructions(admin=True)

        assert 'Editing Configs' in text
        assert 'edit_config' in text

    @override_settings(BUBLIK_FQDN='https://bublik.example', URL_PREFIX='')
    def test_the_guides_follow_the_url_patterns(self):
        text = build_system_instructions(admin=True)

        assert text.index('Bublik URL Patterns') < text.index('Making Changes')
        assert text.index('Making Changes') < text.index('Editing Configs')
        assert text.index('Editing Configs') < text.index('Generating Files')
