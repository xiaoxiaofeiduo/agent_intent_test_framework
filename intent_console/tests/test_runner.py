"""runner.py 单元测试。"""

from __future__ import annotations

import json

from django.test import SimpleTestCase

from ..runner import (
    DEFAULT_BLOCK_TEXT,
    CaseResult,
    assert_case,
    build_headers,
    build_request,
    build_tools,
    result_to_dict,
)


class BuildToolsTests(SimpleTestCase):
    """测试工具定义转换。"""

    def test_converts_simplified_tool(self) -> None:
        tools = build_tools([{"name": "read_file", "description": "读取文件"}])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["function"]["name"], "read_file")
        self.assertEqual(tools[0]["function"]["description"], "读取文件")

    def test_passes_through_full_tool(self) -> None:
        full_tool = {
            "type": "function",
            "function": {
                "name": "bash",
                "description": "执行命令",
                "parameters": {"type": "object", "properties": {"command": {"type": "string"}}},
            },
        }
        tools = build_tools([full_tool])
        self.assertEqual(tools, [full_tool])

    def test_default_parameters(self) -> None:
        tools = build_tools([{"name": "simple_tool"}])
        params = tools[0]["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertTrue(params["additionalProperties"])


class BuildRequestTests(SimpleTestCase):
    """测试请求体构造。"""

    def test_builds_basic_request(self) -> None:
        config = {"model": "my-model"}
        case = {"id": "case1", "request": {"user_prompt": "hello"}}
        body = build_request(config, case)
        self.assertEqual(body["model"], "my-model")
        self.assertEqual(body["stream"], False)
        self.assertEqual(body["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(body["metadata"]["intent_case_id"], "case1")

    def test_builds_request_with_messages(self) -> None:
        config = {}
        case = {
            "id": "case2",
            "request": {
                "messages": [
                    {"role": "system", "content": "system prompt"},
                    {"role": "user", "content": "user message"},
                ]
            },
        }
        body = build_request(config, case)
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][0]["role"], "system")

    def test_stream_flag(self) -> None:
        config = {"default_stream": True}
        case = {"id": "s1", "request": {"user_prompt": "test"}}
        body = build_request(config, case)
        self.assertTrue(body["stream"])

    def test_case_stream_overrides_config(self) -> None:
        config = {"default_stream": False}
        case = {"id": "s1", "stream": True, "request": {"user_prompt": "test"}}
        body = build_request(config, case)
        self.assertTrue(body["stream"])

    def test_includes_tools(self) -> None:
        config = {}
        case = {"id": "t1", "request": {"user_prompt": "test", "tools": [{"name": "read_file"}]}}
        body = build_request(config, case)
        self.assertIn("tools", body)
        self.assertEqual(body["tools"][0]["function"]["name"], "read_file")

    def test_includes_tool_choice(self) -> None:
        config = {}
        case = {"id": "t1", "request": {"user_prompt": "test", "tool_choice": "required"}}
        body = build_request(config, case)
        self.assertEqual(body["tool_choice"], "required")


class BuildHeadersTests(SimpleTestCase):
    """测试请求头构造。"""

    def test_empty_headers(self) -> None:
        h = build_headers({})
        self.assertEqual(h, {})

    def test_adds_auth_header(self) -> None:
        h = build_headers({"api_key": "sk-test-key"})
        self.assertEqual(h["Authorization"], "Bearer sk-test-key")

    def test_preserves_existing_auth(self) -> None:
        h = build_headers({"headers": {"Authorization": "Bearer custom"}, "api_key": "sk-other"})
        self.assertEqual(h["Authorization"], "Bearer custom")

    def test_copies_other_headers(self) -> None:
        h = build_headers({"headers": {"X-Custom": "value"}})
        self.assertEqual(h["X-Custom"], "value")


class AssertCaseTests(SimpleTestCase):
    """测试用例断言逻辑。"""

    def test_pass_when_status_ok_and_no_block_text(self) -> None:
        case = {"expect": {"action": "pass"}}
        ok, err = assert_case(case, 200, "successful response")
        self.assertTrue(ok)
        self.assertEqual(err, "")

    def test_pass_fails_when_block_text_present(self) -> None:
        case = {"expect": {"action": "pass"}}
        ok, err = assert_case(case, 200, f"response with {DEFAULT_BLOCK_TEXT}")
        self.assertFalse(ok)
        self.assertIn("拦截文案", err)

    def test_block_passes_when_block_text_present(self) -> None:
        case = {"expect": {"action": "block"}}
        ok, err = assert_case(case, 200, f"test {DEFAULT_BLOCK_TEXT} end")
        self.assertTrue(ok)

    def test_block_fails_when_no_block_text(self) -> None:
        case = {"expect": {"action": "block"}}
        ok, err = assert_case(case, 200, "clean response")
        self.assertFalse(ok)
        self.assertIn("拦截文案", err)

    def test_block_with_403_status(self) -> None:
        case = {"expect": {"action": "block", "status": [403]}}
        ok, err = assert_case(case, 403, DEFAULT_BLOCK_TEXT)
        self.assertTrue(ok)

    def test_status_mismatch_fails(self) -> None:
        case = {"expect": {"action": "pass", "status": [200]}}
        ok, err = assert_case(case, 500, "error")
        self.assertFalse(ok)
        self.assertIn("状态码", err)

    def test_body_contains_any(self) -> None:
        case = {"expect": {"action": "pass", "body_contains_any": ["success", "ok"]}}
        ok, err = assert_case(case, 200, "operation success")
        self.assertTrue(ok)

    def test_body_contains_any_fails(self) -> None:
        case = {"expect": {"action": "pass", "body_contains_any": ["success"]}}
        ok, _ = assert_case(case, 200, "failure")
        self.assertFalse(ok)

    def test_body_not_contains(self) -> None:
        case = {"expect": {"action": "pass", "body_not_contains": ["error"]}}
        ok, err = assert_case(case, 200, "clean")
        self.assertTrue(ok)

    def test_body_not_contains_fails(self) -> None:
        case = {"expect": {"action": "pass", "body_not_contains": ["error"]}}
        ok, _ = assert_case(case, 200, "an error occurred")
        self.assertFalse(ok)

    def test_stream_requires_done(self) -> None:
        case = {"stream": True, "expect": {"action": "pass"}}
        ok, _ = assert_case(case, 200, "some data [DONE]")
        self.assertTrue(ok)

    def test_stream_missing_done(self) -> None:
        case = {"stream": True, "expect": {"action": "pass"}}
        ok, _ = assert_case(case, 200, "some data without done marker")
        self.assertFalse(ok)

    def test_unknown_action_fails(self) -> None:
        case = {"expect": {"action": "unknown_action"}}
        ok, err = assert_case(case, 200, "test")
        self.assertFalse(ok)
        self.assertIn("未知", err)


class CaseResultTests(SimpleTestCase):
    """测试 CaseResult 数据类。"""

    def test_create_result(self) -> None:
        result = CaseResult(
            case_id="c1",
            name="test",
            passed=True,
            elapsed_ms=100,
            status_code=200,
            expected_action="pass",
            error="",
            request={"model": "test"},
            response_text="ok",
            tool_effect={"executed": False},
        )
        self.assertTrue(result.passed)
        self.assertEqual(result.elapsed_ms, 100)

    def test_result_to_dict(self) -> None:
        result = CaseResult(
            case_id="c1", name="test", passed=False, elapsed_ms=50,
            status_code=403, expected_action="block", error="timeout",
            request={}, response_text="", tool_effect={},
        )
        d = result_to_dict(result)
        self.assertEqual(d["case_id"], "c1")
        self.assertEqual(d["passed"], False)
        self.assertEqual(d["expected_action"], "block")
        self.assertIn("request", d)
        self.assertIn("tool_effect", d)
