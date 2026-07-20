# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.

import uuid

from django.test import SimpleTestCase, TestCase
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from bublik.ai.transcript import _save_messages, serialize_messages


class TranscriptTest(SimpleTestCase):
    def test_serializes_visible_tool_response_history(self):
        messages = [
            ModelRequest(parts=[UserPromptPart(content='Create a report')]),
            ModelResponse(
                parts=[
                    ThinkingPart(content='I should create a file.'),
                    ToolCallPart(
                        tool_name='generate_file',
                        tool_call_id='call-1',
                        args={'filename': 'report.csv'},
                    ),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name='generate_file',
                        tool_call_id='call-1',
                        content={
                            'file_id': 'file-1',
                            'filename': 'report.csv',
                            'download_url': '/api/v2/chat/files/file-1',
                        },
                    )
                ]
            ),
            ModelResponse(parts=[]),
        ]

        transcript = serialize_messages(messages)

        self.assertEqual([message['role'] for message in transcript], ['user', 'assistant'])
        self.assertEqual(
            transcript[0]['parts'], [{'type': 'text', 'content': 'Create a report'}]
        )
        tool_call = transcript[1]['parts'][1]
        self.assertEqual(tool_call['name'], 'generate_file')
        self.assertEqual(tool_call['state'], 'complete')
        self.assertIn('report.csv', tool_call['output'])
        self.assertEqual(transcript[1]['parts'][2]['type'], 'tool-result')


class TranscriptPersistenceTest(TestCase):
    def test_missing_thread_is_ignored(self):
        _save_messages(str(uuid.uuid4()), [])
