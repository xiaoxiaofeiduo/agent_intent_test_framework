#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""可控 Mock 大模型共享逻辑。

提供 OpenAI /v1/chat/completions 接口的模拟响应生成，
供 Django 视图和独立 Mock LLM 服务共用。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .common import compact_json, iter_data_files, load_json_compatible_yaml


DEFAULT_BLOCK_TEXT = "智能体执行行为与用户意图偏离，无法继续执行。"


def load_scenarios(scenarios_dir: str | Path) -> dict[str, dict[str, Any]]:
    """加载所有场景，并按 id 建立索引。"""
    scenarios: dict[str, dict[str, Any]] = {}
    for file_path in iter_data_files(scenarios_dir):
        data = load_json_compatible_yaml(file_path)
        if isinstance(data, dict) and "cases" in data:
            case_list = data["cases"]
        elif isinstance(data, list):
            case_list = data
        else:
            raise ValueError(f"{file_path} 必须是数组，或包含 cases 数组")

        for case in case_list:
            case_id = case.get("id")
            if not case_id:
                raise ValueError(f"{file_path} 中存在缺少 id 的场景")
            if case_id in scenarios:
                raise ValueError(f"场景 id 重复: {case_id}")
            scenarios[case_id] = case
    return scenarios


def extract_user_prompt(body: dict[str, Any]) -> str:
    """提取最后一条 user 消息文本，用于兜底匹配场景。"""
    prompt = ""
    for message in body.get("messages", []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            prompt = content
        elif isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            prompt = "\n".join(parts)
    return prompt


def find_case(body: dict[str, Any], scenarios: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """根据 metadata.intent_case_id 或 user prompt 选择场景。"""
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    case_id = metadata.get("intent_case_id")
    if case_id and case_id in scenarios:
        return scenarios[case_id]

    user_prompt = extract_user_prompt(body)
    for case in scenarios.values():
        request = case.get("request", {})
        expected_prompt = request.get("user_prompt")
        if expected_prompt and expected_prompt == user_prompt:
            return case
        prompt_contains = request.get("prompt_contains")
        if prompt_contains and prompt_contains in user_prompt:
            return case

    return {
        "id": "default",
        "mock_response": {
            "content": "这是 Mock LLM 默认响应，未匹配到指定测试场景。"
        },
    }


def normalize_tool_calls(case: dict[str, Any]) -> list[dict[str, Any]]:
    """把场景中的 tool_calls 转成 OpenAI 响应结构。"""
    response = case.get("mock_response", {})
    tool_calls = []
    for index, item in enumerate(response.get("tool_calls", [])):
        function_name = item.get("name") or item.get("function", {}).get("name")
        arguments = item.get("arguments", {})
        if isinstance(arguments, str):
            arguments_text = arguments
        else:
            arguments_text = compact_json(arguments)
        tool_calls.append(
            {
                "id": item.get("id", f"call_{index + 1}"),
                "type": "function",
                "function": {
                    "name": function_name,
                    "arguments": arguments_text,
                },
            }
        )
    return tool_calls


def should_mock_protect(body: dict[str, Any], case: dict[str, Any]) -> bool:
    """自动化自测直连 Mock LLM 时，按用例期望模拟防护侧拦截。"""
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    return bool(metadata.get("intent_mock_protection")) and expect.get("action") == "block"


def block_text_for_case(case: dict[str, Any]) -> str:
    """读取用例配置里的拦截文案。"""
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    return str(expect.get("block_text") or DEFAULT_BLOCK_TEXT)


def build_non_stream_response(body: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """构造非流式 OpenAI Chat Completions 响应。"""
    response = case.get("mock_response", {})
    protected = should_mock_protect(body, case)
    tool_calls = [] if protected else normalize_tool_calls(case)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": block_text_for_case(case) if protected else response.get("content"),
    }
    finish_reason = "stop"
    if tool_calls:
        message["content"] = response.get("content")
        message["tool_calls"] = tool_calls
        finish_reason = "tool_calls"

    return {
        "id": f"chatcmpl-mock-{case.get('id', 'default')}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock-agent-intent-model"),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
    }


def build_stream_events(body: dict[str, Any], case: dict[str, Any]) -> list[dict[str, Any]]:
    """构造流式 SSE 事件列表。"""
    model = body.get("model", "mock-agent-intent-model")
    base = {
        "id": f"chatcmpl-mock-{case.get('id', 'default')}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
    }
    events: list[dict[str, Any]] = [
        {
            **base,
            "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
        }
    ]

    response = case.get("mock_response", {})
    protected = should_mock_protect(body, case)
    content = block_text_for_case(case) if protected else response.get("content")
    if content:
        events.append(
            {
                **base,
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
        )

    tool_calls = [] if protected else normalize_tool_calls(case)
    for index, tool_call in enumerate(tool_calls):
        events.append(
            {
                **base,
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": tool_call["id"],
                                    "type": "function",
                                    "function": tool_call["function"],
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
        )

    finish_reason = "tool_calls" if tool_calls else "stop"
    events.append(
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
    )
    return events
