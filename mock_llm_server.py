#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""可控 Mock 大模型服务。

该服务模拟 OpenAI /v1/chat/completions 接口，按场景返回指定 tool_calls。
防护设备应配置为把大模型上游指向本服务，从而触发真实的意图识别链路。
"""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from common import compact_json, iter_data_files, load_json_compatible_yaml


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


def build_non_stream_response(body: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """构造非流式 OpenAI Chat Completions 响应。"""
    response = case.get("mock_response", {})
    tool_calls = normalize_tool_calls(case)
    message: dict[str, Any] = {
        "role": "assistant",
        "content": response.get("content"),
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
    content = response.get("content")
    if content:
        events.append(
            {
                **base,
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
            }
        )

    for index, tool_call in enumerate(normalize_tool_calls(case)):
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

    finish_reason = "tool_calls" if normalize_tool_calls(case) else "stop"
    events.append(
        {
            **base,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
        }
    )
    return events


class MockLLMHandler(BaseHTTPRequestHandler):
    """处理 OpenAI-compatible 请求。"""

    scenarios: dict[str, dict[str, Any]] = {}

    def log_message(self, fmt: str, *args: Any) -> None:
        """使用中文前缀输出访问日志。"""
        print(f"[Mock LLM] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        """提供简单健康检查接口。"""
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write("ok".encode("utf-8"))
            return
        self.send_error(404, "not found")

    def do_POST(self) -> None:
        """处理 /v1/chat/completions。"""
        if self.path.rstrip("/") != "/v1/chat/completions":
            self.send_error(404, "not found")
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return

        case = find_case(body, self.scenarios)
        delay_ms = int(case.get("mock_response", {}).get("delay_ms", 0) or 0)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000)

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for event in build_stream_events(body, case):
                self.wfile.write(f"data: {compact_json(event)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        payload = compact_json(build_non_stream_response(body, case)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def main() -> None:
    """启动 Mock LLM 服务。"""
    parser = argparse.ArgumentParser(description="启动智能体意图识别 Mock LLM 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=18080, help="监听端口")
    parser.add_argument("--scenarios-dir", default="scenarios", help="场景目录")
    args = parser.parse_args()

    MockLLMHandler.scenarios = load_scenarios(args.scenarios_dir)
    server = ThreadingHTTPServer((args.host, args.port), MockLLMHandler)
    print(f"Mock LLM 已启动: http://{args.host}:{args.port}/v1/chat/completions")
    print(f"已加载场景数: {len(MockLLMHandler.scenarios)}")
    server.serve_forever()


if __name__ == "__main__":
    main()

