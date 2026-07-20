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
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolResultEvent,
    ToolReturnPart,
    UserPromptPart,
)

from bublik.data.models import AiChatThread


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage, ModelRequestPart, ModelResponsePart


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


class PartialRun:
    """Accumulates a run's messages from the native Pydantic AI event stream.

    ``on_complete`` only fires when a run finishes cleanly, so a cancelled or
    failed run has no ``result.new_messages()`` to persist. Tapping the native
    event stream keeps an always-available approximation of the same history:
    what the model produced up to the interruption, including tool calls whose
    side effects (an uploaded file, say) already happened.

    The events arrive in step order -- response parts, then the tool results
    that answer them, then the next response -- so a switch between the two
    kinds closes the message being built and opens the next one.
    """

    def __init__(self) -> None:
        self.messages: list[ModelMessage] = []
        self._parts: dict[int, ModelResponsePart] = {}
        self._returns: list[ModelRequestPart] = []

    def _flush_response(self) -> None:
        if self._parts:
            self.messages.append(ModelResponse(parts=list(self._parts.values())))
            self._parts = {}

    def _flush_returns(self) -> None:
        if self._returns:
            self.messages.append(ModelRequest(parts=self._returns))
            self._returns = []

    def add(self, event: object) -> None:
        """Fold one native agent event into the accumulated history."""
        if isinstance(event, PartStartEvent):
            # A response part after tool results means a new step started.
            self._flush_returns()
            # Repeating an index replaces the part, per PartStartEvent's contract.
            self._parts[event.index] = event.part
        elif isinstance(event, PartDeltaEvent):
            current = self._parts.get(event.index)
            if current is not None:
                self._parts[event.index] = event.delta.apply(current)
        elif isinstance(event, ToolResultEvent):
            self._flush_response()
            self._returns.append(event.part)

    def snapshot(self) -> list[ModelMessage]:
        """The history accumulated so far, with any in-progress message closed."""
        self._flush_response()
        self._flush_returns()
        return list(self.messages)


def _save_messages(thread_id: str, messages: list[dict]) -> None:
    thread = AiChatThread.objects.filter(pk=thread_id).first()
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
