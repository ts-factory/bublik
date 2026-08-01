# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 OKTET Labs Ltd. All rights reserved.
"""Server-owned serialization of agent history into the chat UI transcript."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import uuid4

from asgiref.sync import sync_to_async
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from bublik.data.models import ChatThread


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage


def _id() -> str:
    return f'msg-{uuid4()}'


def _content(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)


def _find_tool_call(messages: list[dict], tool_call_id: str) -> dict | None:
    for message in reversed(messages):
        if message['role'] != 'assistant':
            continue
        for part in message['parts']:
            if part['type'] == 'tool-call' and part['id'] == tool_call_id:
                return part
    return None


def _append_tool_result(
    transcript: list[dict],
    part: ToolReturnPart | NativeToolReturnPart | RetryPromptPart,
) -> None:
    if isinstance(part, (ToolReturnPart, NativeToolReturnPart)):
        state = 'complete'
        content = _content(part.content)
        error = None
    else:
        state = 'error'
        content = _content(part.content)
        error = content
    tool_call = _find_tool_call(transcript, part.tool_call_id)
    result = {
        'type': 'tool-result',
        'toolCallId': part.tool_call_id,
        'content': content,
        'state': state,
        **({'error': error} if error else {}),
    }
    if tool_call is None:
        transcript.append({'id': _id(), 'role': 'assistant', 'parts': [result]})
        return
    tool_call['state'] = state
    tool_call['output'] = content
    if error:
        tool_call['error'] = error
    for candidate in reversed(transcript):
        if tool_call in candidate['parts']:
            candidate['parts'].append(result)
            return


def serialize_messages(messages: list[ModelMessage]) -> list[dict]:
    """Convert Pydantic AI history into the stable ``UIMessage[]`` wire shape."""
    transcript: list[dict] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            parts: list[dict] = []
            for part in message.parts:
                if isinstance(part, ThinkingPart):
                    parts.append(
                        {
                            'type': 'thinking',
                            'content': part.content,
                            **({'signature': part.signature} if part.signature else {}),
                        }
                    )
                elif isinstance(part, TextPart):
                    parts.append({'type': 'text', 'content': part.content})
                elif isinstance(part, (ToolCallPart, NativeToolCallPart)):
                    parts.append(
                        {
                            'type': 'tool-call',
                            'id': part.tool_call_id,
                            'name': part.tool_name,
                            'arguments': part.args_as_json_str(),
                            'state': 'input-complete',
                        }
                    )
            if parts:
                transcript.append({'id': _id(), 'role': 'assistant', 'parts': parts})
            continue

        if not isinstance(message, ModelRequest):
            continue
        user_parts = [part for part in message.parts if isinstance(part, UserPromptPart)]
        if user_parts:
            parts = [{'type': 'text', 'content': _content(part.content)} for part in user_parts]
            transcript.append({'id': _id(), 'role': 'user', 'parts': parts})

        for part in message.parts:
            if isinstance(part, (ToolReturnPart, NativeToolReturnPart, RetryPromptPart)):
                _append_tool_result(transcript, part)
    return transcript


def _save_messages(thread_id: str, messages: list[dict]) -> None:
    thread = ChatThread.objects.filter(pk=thread_id).first()
    if thread is None:
        return
    thread.messages = messages
    update_fields = ['messages', 'updated']
    if not thread.title:
        for message in messages:
            if message['role'] != 'user':
                continue
            text = ' '.join(
                part['content'] for part in message['parts'] if part['type'] == 'text'
            ).strip()
            if text:
                thread.title = text[:60]
                update_fields.append('title')
                break
    thread.save(update_fields=update_fields)


async def persist_messages(thread_id: str, messages: list[ModelMessage]) -> None:
    """Persist one complete visible transcript and refresh the thread timestamp."""
    await sync_to_async(_save_messages)(thread_id, serialize_messages(messages))
