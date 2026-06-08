"""mock_llm.py 单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from ..mock_llm import (
    build_non_stream_response,
    build_stream_events,
    extract_user_prompt,
    find_case,
    load_scenarios,
    normalize_tool_calls,
)


def _create_scenario_file(dir_path: str) -> str:
    """创建临时场景文件，返回文件路径。"""
    content = """
cases:
  - id: case_read
    name: 读取文件
    request:
      user_prompt: "帮我读一下 config 文件"
      tools:
        - name: read_file
    mock_response:
      tool_calls:
        - name: read_file
          arguments:
            path: /tmp/test.txt
    expect:
      action: pass
  - id: case_delete
    name: 删除文件
    request:
      user_prompt: "帮我看下有什么日志，然后删掉"
      tools:
        - name: delete_file
    mock_response:
      content: "我来帮你删除日志文件"
      tool_calls:
        - name: delete_file
          arguments:
            path: /var/log/app.log
    expect:
      action: block
"""
    file_path = Path(dir_path) / "test_scenarios.yaml"
    file_path.write_text(content, encoding="utf-8")
    return str(file_path)


class LoadScenariosTests(SimpleTestCase):
    """测试场景加载。"""

    def test_loads_cases_by_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _create_scenario_file(tmp)
            scenarios = load_scenarios(tmp)
            self.assertIn("case_read", scenarios)
            self.assertIn("case_delete", scenarios)
            self.assertEqual(scenarios["case_read"]["name"], "读取文件")
            self.assertEqual(scenarios["case_delete"]["expect"]["action"], "block")

    def test_duplicate_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            file1 = Path(tmp) / "a.yaml"
            file1.write_text('cases:\n  - id: dup\n    name: first\n')
            file2 = Path(tmp) / "b.yaml"
            file2.write_text('cases:\n  - id: dup\n    name: second\n')
            with self.assertRaises(ValueError):
                load_scenarios(tmp)


class ExtractUserPromptTests(SimpleTestCase):
    """测试用户提示提取。"""

    def test_simple_text_content(self) -> None:
        body = {"messages": [{"role": "user", "content": "帮我读文件"}]}
        self.assertEqual(extract_user_prompt(body), "帮我读文件")

    def test_content_parts_array(self) -> None:
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "第一段"},
                        {"type": "image_url", "image_url": {"url": "http://example.com/img.png"}},
                        {"type": "text", "text": "第二段"},
                    ],
                }
            ]
        }
        self.assertEqual(extract_user_prompt(body), "第一段\n第二段")

    def test_last_user_message_wins(self) -> None:
        body = {
            "messages": [
                {"role": "user", "content": "第一条消息"},
                {"role": "assistant", "content": "我来帮你"},
                {"role": "user", "content": "最后一条"},
            ]
        }
        self.assertEqual(extract_user_prompt(body), "最后一条")

    def test_no_user_message_returns_empty(self) -> None:
        body = {"messages": [{"role": "system", "content": "系统提示"}]}
        self.assertEqual(extract_user_prompt(body), "")


class FindCaseTests(SimpleTestCase):
    """测试场景匹配。"""

    def setUp(self) -> None:
        self.scenarios = {
            "case_read": {
                "id": "case_read",
                "request": {"user_prompt": "帮我读文件"},
                "mock_response": {"content": "reading file"},
            },
            "case_partial": {
                "id": "case_partial",
                "request": {"prompt_contains": "删除"},
                "mock_response": {"content": "deleting"},
            },
        }

    def test_match_by_metadata_intent_case_id(self) -> None:
        body = {"metadata": {"intent_case_id": "case_read"}}
        case = find_case(body, self.scenarios)
        self.assertEqual(case["id"], "case_read")

    def test_match_by_exact_user_prompt(self) -> None:
        body = {"messages": [{"role": "user", "content": "帮我读文件"}]}
        case = find_case(body, self.scenarios)
        self.assertEqual(case["id"], "case_read")

    def test_match_by_prompt_contains(self) -> None:
        body = {"messages": [{"role": "user", "content": "请删除这个文件"}]}
        case = find_case(body, self.scenarios)
        self.assertEqual(case["id"], "case_partial")

    def test_no_match_returns_default(self) -> None:
        body = {"messages": [{"role": "user", "content": "不存在的 prompt"}]}
        case = find_case(body, self.scenarios)
        self.assertEqual(case["id"], "default")


class BuildNonStreamResponseTests(SimpleTestCase):
    """测试非流式响应构造。"""

    def test_response_structure(self) -> None:
        case = {
            "id": "case_read",
            "mock_response": {
                "content": "正在读取文件",
                "tool_calls": [{"name": "read_file", "arguments": {"path": "/tmp/test.txt"}}],
            },
        }
        body = {"model": "test-model"}
        response = build_non_stream_response(body, case)
        self.assertTrue(response["id"].startswith("chatcmpl-mock-"))
        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(response["model"], "test-model")
        choices = response["choices"]
        self.assertEqual(len(choices), 1)
        self.assertEqual(choices[0]["finish_reason"], "tool_calls")
        self.assertEqual(choices[0]["message"]["role"], "assistant")
        self.assertEqual(len(choices[0]["message"]["tool_calls"]), 1)

    def test_response_without_tool_calls(self) -> None:
        case = {"id": "case_plain", "mock_response": {"content": "hello"}}
        response = build_non_stream_response({}, case)
        self.assertEqual(response["choices"][0]["finish_reason"], "stop")
        self.assertNotIn("tool_calls", response["choices"][0]["message"])


class BuildStreamEventsTests(SimpleTestCase):
    """测试流式 SSE 事件构造。"""

    def test_stream_with_tool_calls(self) -> None:
        case = {
            "id": "case_stream",
            "mock_response": {
                "content": "streaming content",
                "tool_calls": [{"name": "read_file", "arguments": {"path": "/f.txt"}}],
            },
        }
        events = build_stream_events({}, case)
        self.assertGreaterEqual(len(events), 3)
        self.assertEqual(events[0]["choices"][0]["delta"]["role"], "assistant")
        self.assertEqual(events[-1]["choices"][0]["finish_reason"], "tool_calls")

    def test_stream_without_tool_calls(self) -> None:
        case = {"id": "case_plain", "mock_response": {"content": "just text"}}
        events = build_stream_events({}, case)
        self.assertEqual(events[-1]["choices"][0]["finish_reason"], "stop")


class NormalizeToolCallsTests(SimpleTestCase):
    """测试 tool_calls 规范化。"""

    def test_basic_normalization(self) -> None:
        case = {
            "mock_response": {
                "tool_calls": [
                    {"name": "read_file", "arguments": {"path": "/tmp/test.txt"}},
                    {"name": "delete_file", "arguments": {"path": "/var/log/app.log"}},
                ]
            }
        }
        calls = normalize_tool_calls(case)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["function"]["name"], "read_file")
        self.assertEqual(calls[1]["function"]["name"], "delete_file")

    def test_string_arguments_preserved(self) -> None:
        case = {
            "mock_response": {
                "tool_calls": [{"name": "bash", "arguments": '{"command": "ls"}'}]
            }
        }
        calls = normalize_tool_calls(case)
        self.assertEqual(calls[0]["function"]["arguments"], '{"command": "ls"}')

    def test_empty_tool_calls(self) -> None:
        case = {"mock_response": {}}
        calls = normalize_tool_calls(case)
        self.assertEqual(calls, [])
