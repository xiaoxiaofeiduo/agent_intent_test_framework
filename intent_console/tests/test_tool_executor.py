"""tool_executor.py 单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from ..tool_executor import (
    MockToolExecutor,
    build_file_diff,
    extract_tool_calls,
    normalize_openai_tool_calls,
    resolve_mock_path,
    safe_name,
    seed_workspace,
    snapshot_files,
)


class ExtractToolCallsTests(SimpleTestCase):
    """测试工具调用提取。"""

    def test_extract_from_non_stream_response(self) -> None:
        response = json.dumps({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "/tmp/f.txt"}'}},
                    ]
                }
            }]
        })
        calls = extract_tool_calls(response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "read_file")
        self.assertEqual(calls[0]["arguments"], {"path": "/tmp/f.txt"})

    def test_extract_from_stream_response(self) -> None:
        response = '\n'.join([
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"path\\":\\"/tmp/f.txt\\"}"}}]}}]}',
            'data: {"choices":[{"finish_reason":"tool_calls"}]}',
            'data: [DONE]',
        ])
        calls = extract_tool_calls(response)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "read_file")
        self.assertEqual(calls[0]["arguments"], {"path": "/tmp/f.txt"})

    def test_block_text_skips_extraction(self) -> None:
        response = "智能体执行行为与用户意图偏离，无法继续执行。"
        calls = extract_tool_calls(response)
        self.assertEqual(calls, [])

    def test_empty_response(self) -> None:
        self.assertEqual(extract_tool_calls(""), [])
        self.assertEqual(extract_tool_calls(None), [])  # type: ignore[arg-type]

    def test_invalid_json_returns_empty(self) -> None:
        self.assertEqual(extract_tool_calls("not json"), [])


class NormalizeOpenaiToolCallsTests(SimpleTestCase):
    """测试 tool_calls 规范化。"""

    def test_normalizes_openai_format(self) -> None:
        calls = normalize_openai_tool_calls([
            {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": '{"command":"ls"}'}}
        ])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "call_1")
        self.assertEqual(calls[0]["name"], "bash")
        self.assertEqual(calls[0]["arguments"], {"command": "ls"})

    def test_invalid_arguments_kept_as_string(self) -> None:
        calls = normalize_openai_tool_calls([
            {"id": "call_1", "function": {"name": "test", "arguments": "not valid json"}}
        ])
        self.assertEqual(calls[0]["arguments"], "not valid json")

    def test_non_list_returns_empty(self) -> None:
        self.assertEqual(normalize_openai_tool_calls(None), [])
        self.assertEqual(normalize_openai_tool_calls("not a list"), [])


class MockToolExecutorTests(SimpleTestCase):
    """测试 Mock 工具执行器。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.executor = MockToolExecutor(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_read_file(self) -> None:
        case_dir = Path(self.tmp.name) / "read_test"
        case_dir.mkdir(parents=True)
        (case_dir / "test.txt").write_text("hello world")
        result = self.executor.read_file(case_dir, {"path": "/test.txt"})
        self.assertIn("hello world", result)

    def test_read_nonexistent_file(self) -> None:
        case_dir = Path(self.tmp.name) / "missing_test"
        case_dir.mkdir(parents=True)
        result = self.executor.read_file(case_dir, {"path": "/no_such_file.txt"})
        self.assertIn("文件不存在", result)

    def test_write_file(self) -> None:
        case_dir = Path(self.tmp.name) / "write_test"
        case_dir.mkdir(parents=True)
        result = self.executor.write_file(case_dir, {"path": "/out.txt", "content": "created"})
        self.assertIn("已写入", result)
        self.assertTrue((case_dir / "out.txt").exists())
        self.assertEqual((case_dir / "out.txt").read_text(), "created")

    def test_delete_file(self) -> None:
        case_dir = Path(self.tmp.name) / "delete_test"
        case_dir.mkdir(parents=True)
        (case_dir / "remove_me.txt").write_text("temp")
        result = self.executor.delete_file(case_dir, {"path": "/remove_me.txt"})
        self.assertIn("已删除文件", result)
        self.assertFalse((case_dir / "remove_me.txt").exists())

    def test_delete_directory(self) -> None:
        case_dir = Path(self.tmp.name) / "delete_dir_test"
        target = case_dir / "subdir"
        target.mkdir(parents=True)
        (target / "file.txt").write_text("content")
        result = self.executor.delete_file(case_dir, {"path": "/subdir"})
        self.assertIn("已删除目录", result)
        self.assertFalse(target.exists())

    def test_execute_tool_dispatch(self) -> None:
        case_dir = Path(self.tmp.name) / "dispatch_test"
        case_dir.mkdir(parents=True)
        result = self.executor.execute_tool(case_dir, {"id": "c1", "name": "ps", "arguments": {}})
        self.assertTrue(result["ok"])
        self.assertIn("mock-agent", result["output"])

    def test_execute_case_full_lifecycle(self) -> None:
        case = {
            "id": "full_test",
            "mock_workspace": {
                "files": {"data.txt": "original content"}
            },
        }
        response = json.dumps({
            "choices": [{
                "message": {
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "/data.txt"}'}},
                    ]
                }
            }]
        })
        effect = self.executor.execute_case(case, response)
        self.assertTrue(effect["executed"])
        self.assertTrue(effect["restored"])
        self.assertEqual(effect["tool_calls_count"], 1)
        self.assertEqual(len(effect["results"]), 1)
        self.assertTrue(effect["results"][0]["ok"])


class ResolveMockPathTests(SimpleTestCase):
    """测试路径解析与安全。"""

    def setUp(self) -> None:
        # resolve() 处理 macOS 上 /tmp -> /private/tmp 的符号链接
        self.case_dir = Path(tempfile.mkdtemp()).resolve()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.case_dir)

    def test_resolves_relative_path(self) -> None:
        resolved = resolve_mock_path(self.case_dir, "tmp/file.txt")
        self.assertEqual(resolved, self.case_dir / "tmp" / "file.txt")

    def test_resolves_absolute_path_inside_workspace(self) -> None:
        resolved = resolve_mock_path(self.case_dir, "/tmp/data.txt")
        self.assertEqual(resolved, self.case_dir / "tmp" / "data.txt")

    def test_path_traversal_blocked(self) -> None:
        with self.assertRaises(ValueError):
            resolve_mock_path(self.case_dir, "../../etc/passwd")

    def test_empty_path_uses_unknown(self) -> None:
        resolved = resolve_mock_path(self.case_dir, "")
        self.assertEqual(resolved, self.case_dir / "unknown")

    def test_none_path_uses_unknown(self) -> None:
        resolved = resolve_mock_path(self.case_dir, None)  # type: ignore[arg-type]
        self.assertEqual(resolved, self.case_dir / "unknown")


class SeedWorkspaceTests(SimpleTestCase):
    """测试工作区初始化。"""

    def test_seeds_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "seed_test"
            case_dir.mkdir()
            case = {"id": "test", "mock_workspace": {"files": {"a.txt": "content A", "sub/b.txt": "content B"}}}
            seed_workspace(case_dir, case)
            self.assertTrue((case_dir / "a.txt").exists())
            self.assertTrue((case_dir / "sub" / "b.txt").exists())
            self.assertEqual((case_dir / "a.txt").read_text(), "content A")
            self.assertTrue((case_dir / "README.txt").exists())

    def test_seeds_empty_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "empty_test"
            case_dir.mkdir()
            case = {"id": "test"}
            seed_workspace(case_dir, case)
            self.assertTrue((case_dir / "README.txt").exists())


class SnapshotFilesTests(SimpleTestCase):
    """测试文件快照。"""

    def test_snapshots_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp)
            (case_dir / "a.txt").write_text("hello")
            (case_dir / "b.txt").write_text("world")
            snap = snapshot_files(case_dir)
            paths = [s["path"] for s in snap]
            self.assertIn("a.txt", paths)
            self.assertIn("b.txt", paths)

    def test_nonexistent_returns_empty(self) -> None:
        snap = snapshot_files(Path("/nonexistent"))
        self.assertEqual(snap, [])


class BuildFileDiffTests(SimpleTestCase):
    """测试文件差异构造。"""

    def test_detects_created_file(self) -> None:
        before = [{"path": "a.txt", "size": 5, "preview": "hello"}]
        after = [
            {"path": "a.txt", "size": 5, "preview": "hello"},
            {"path": "b.txt", "size": 5, "preview": "world"},
        ]
        restored = [{"path": "a.txt", "size": 5, "preview": "hello"}]
        diff = build_file_diff(before, after, restored)
        self.assertEqual(diff["summary"]["created"], 1)
        self.assertEqual(diff["summary"]["unchanged"], 1)

    def test_detects_modified_file(self) -> None:
        before = [{"path": "a.txt", "size": 5, "preview": "hello"}]
        after = [{"path": "a.txt", "size": 10, "preview": "hello world"}]
        restored = [{"path": "a.txt", "size": 5, "preview": "hello"}]
        diff = build_file_diff(before, after, restored)
        self.assertEqual(diff["summary"]["modified"], 1)
        self.assertEqual(diff["summary"]["restored"], 1)

    def test_detects_deleted_file(self) -> None:
        before = [{"path": "a.txt", "size": 5, "preview": "hello"}, {"path": "b.txt", "size": 5, "preview": "byebye"}]
        after = [{"path": "a.txt", "size": 5, "preview": "hello"}]
        restored = [{"path": "a.txt", "size": 5, "preview": "hello"}, {"path": "b.txt", "size": 5, "preview": "byebye"}]
        diff = build_file_diff(before, after, restored)
        self.assertEqual(diff["summary"]["deleted"], 1)


class SafeNameTests(SimpleTestCase):
    """测试安全名称生成。"""

    def test_replaces_special_chars(self) -> None:
        self.assertEqual(safe_name("hello world!"), "hello_world_")
        self.assertEqual(safe_name("test/case?query=1"), "test_case_query_1")

    def test_preserves_safe_chars(self) -> None:
        self.assertEqual(safe_name("case_1.0-test"), "case_1.0-test")
