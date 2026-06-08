#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""受控工具执行模拟器。

所有工具副作用都限制在 mock_workspace 目录下，避免测试用例误操作真实系统。
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .common import compact_json


BLOCK_TEXT = "智能体执行行为与用户意图偏离，无法继续执行。"


def extract_tool_calls(response_text: str) -> list[dict[str, Any]]:
    """从 OpenAI 非流式响应或 SSE 流式响应中提取工具调用。"""
    if not response_text or BLOCK_TEXT in response_text:
        return []
    if "data: " in response_text:
        return extract_stream_tool_calls(response_text)
    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        return []
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return []
    message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
    return normalize_openai_tool_calls(message.get("tool_calls", []))


def extract_stream_tool_calls(response_text: str) -> list[dict[str, Any]]:
    """从 SSE chunk 中合并 OpenAI tool_calls。"""
    calls: dict[int, dict[str, Any]] = {}
    for line in response_text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        choices = event.get("choices") if isinstance(event, dict) else None
        if not choices:
            continue
        delta = choices[0].get("delta", {}) if isinstance(choices[0], dict) else {}
        for item in delta.get("tool_calls", []) or []:
            index = int(item.get("index", 0))
            current = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if item.get("id"):
                current["id"] = item["id"]
            function = item.get("function") or {}
            if function.get("name"):
                current["function"]["name"] = function["name"]
            if "arguments" in function:
                current["function"]["arguments"] += function.get("arguments") or ""
    return normalize_openai_tool_calls([calls[index] for index in sorted(calls)])


def normalize_openai_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    """把 OpenAI tool_calls 转为统一结构。"""
    normalized: list[dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return normalized
    for index, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name") or ""
        arguments = function.get("arguments", call.get("arguments", {}))
        if isinstance(arguments, str):
            try:
                arguments_value = json.loads(arguments)
            except json.JSONDecodeError:
                arguments_value = arguments
        else:
            arguments_value = arguments
        normalized.append(
            {
                "id": call.get("id", f"call_{index + 1}"),
                "name": name,
                "arguments": arguments_value,
            }
        )
    return normalized


class MockToolExecutor:
    """在 mock_workspace 中模拟执行工具调用。"""

    def __init__(self, workspace_root: str | Path) -> None:
        self.workspace_root = Path(workspace_root)

    def execute_case(self, case: dict[str, Any], response_text: str) -> dict[str, Any]:
        """执行单个用例响应里的工具调用，并返回执行效果。"""
        tool_calls = extract_tool_calls(response_text)
        case_dir = self.workspace_root / safe_name(case.get("id", "case"))
        reset_dir(case_dir)
        seed_workspace(case_dir, case)
        before_files = snapshot_files(case_dir)

        results = []
        for call in tool_calls:
            results.append(self.execute_tool(case_dir, call))
        after_files = snapshot_files(case_dir)

        reset_dir(case_dir)
        seed_workspace(case_dir, case)
        restored_files = snapshot_files(case_dir)
        diff = build_file_diff(before_files, after_files, restored_files)

        return {
            "workspace": str(case_dir),
            "executed": bool(results),
            "restored": True,
            "tool_calls_count": len(tool_calls),
            "results": results,
            "before_files": before_files,
            "files": after_files,
            "restored_files": restored_files,
            "file_diff": diff["files"],
            "file_summary": diff["summary"],
        }

    def execute_tool(self, case_dir: Path, call: dict[str, Any]) -> dict[str, Any]:
        """按工具名分发执行。"""
        name = call.get("name", "")
        arguments = call.get("arguments", {})
        started = time.time()
        try:
            if name in {"read_file", "read", "cat"}:
                output = self.read_file(case_dir, arguments)
            elif name in {"write_file", "create_file", "edit_file"}:
                output = self.write_file(case_dir, arguments)
            elif name in {"delete_file", "remove_file"}:
                output = self.delete_file(case_dir, arguments)
            elif name in {"bash", "shell", "sh"}:
                output = self.run_bash(case_dir, arguments)
            elif name in {"sudo"}:
                output = self.record_event(case_dir, "permission.log", compact_json(arguments))
            elif name in {"ps"}:
                output = "PID CMD\n1 mock-init\n42 mock-agent\n"
            else:
                output = self.record_event(case_dir, "unknown_tools.log", compact_json({"name": name, "arguments": arguments}))
            ok = True
            error = ""
        except Exception as exc:  # noqa: BLE001 - 测试工具需要把异常展示给页面
            ok = False
            output = ""
            error = f"{type(exc).__name__}: {exc}"

        return {
            "id": call.get("id", ""),
            "name": name,
            "arguments": arguments,
            "ok": ok,
            "elapsed_ms": int((time.time() - started) * 1000),
            "output": output,
            "error": error,
        }

    def read_file(self, case_dir: Path, arguments: Any) -> str:
        """模拟读取文件。"""
        path = resolve_mock_path(case_dir, pick_path(arguments))
        if not path.exists():
            return f"文件不存在: {display_mock_path(case_dir, path)}"
        return path.read_text(encoding="utf-8", errors="replace")

    def write_file(self, case_dir: Path, arguments: Any) -> str:
        """模拟写入文件。"""
        path = resolve_mock_path(case_dir, pick_path(arguments))
        content = arguments.get("content", "") if isinstance(arguments, dict) else ""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
        return f"已写入: {display_mock_path(case_dir, path)}"

    def delete_file(self, case_dir: Path, arguments: Any) -> str:
        """模拟删除文件或目录。"""
        path = resolve_mock_path(case_dir, pick_path(arguments))
        if path.is_dir():
            shutil.rmtree(path)
            return f"已删除目录: {display_mock_path(case_dir, path)}"
        if path.exists():
            path.unlink()
            return f"已删除文件: {display_mock_path(case_dir, path)}"
        return f"目标不存在，无需删除: {display_mock_path(case_dir, path)}"

    def run_bash(self, case_dir: Path, arguments: Any) -> str:
        """模拟常见 shell 命令，不调用真实 shell。"""
        command = arguments.get("command", "") if isinstance(arguments, dict) else str(arguments)
        append_file(case_dir / "command_history.log", command + "\n")

        rm_match = re.search(r"\brm\s+(?:-[rfRF]+\s+)*([^\s;&|]+)", command)
        if rm_match:
            return self.delete_file(case_dir, {"path": rm_match.group(1)})

        cat_match = re.search(r"\bcat\s+([^\s;&|]+)", command)
        if cat_match:
            return self.read_file(case_dir, {"path": cat_match.group(1)})

        if command.startswith("ls") or " ls " in command:
            return "\n".join(item["path"] for item in snapshot_files(case_dir)) or "(empty)"

        if command.startswith("du") or " du " in command:
            return f"{sum(item['size'] for item in snapshot_files(case_dir))}\t."

        if "npm install" in command:
            marker = case_dir / "node_modules" / ".mock-install"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("npm install 已模拟执行\n", encoding="utf-8")
            return "added mock packages"

        if command.startswith("curl") or " curl " in command:
            return self.record_event(case_dir, "network_requests.log", command)

        if command.startswith("chmod") or " chmod " in command:
            return self.record_event(case_dir, "permission_changes.log", command)

        if "nc -l" in command:
            return self.record_event(case_dir, "network_listen.log", command)

        if "/dev/tcp/" in command:
            return self.record_event(case_dir, "network_connect.log", command)

        return self.record_event(case_dir, "shell_unhandled.log", command)

    def record_event(self, case_dir: Path, filename: str, content: str) -> str:
        """记录模拟副作用。"""
        path = case_dir / filename
        append_file(path, content + "\n")
        return f"已记录模拟事件: {filename}"


def seed_workspace(case_dir: Path, case: dict[str, Any]) -> None:
    """初始化用例 mock 工作目录。"""
    files = case.get("mock_workspace", {}).get("files", {}) if isinstance(case.get("mock_workspace"), dict) else {}
    if isinstance(files, dict):
        for path_text, content in files.items():
            path = resolve_mock_path(case_dir, path_text)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
    append_file(case_dir / "README.txt", f"mock workspace for {case.get('id', 'case')}\n")


def snapshot_files(case_dir: Path) -> list[dict[str, Any]]:
    """返回 mock 工作目录文件快照。"""
    files = []
    if not case_dir.exists():
        return files
    root = case_dir.resolve()
    for path in sorted(item for item in case_dir.rglob("*") if item.is_file()):
        rel = path.resolve().relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        files.append({"path": rel, "size": path.stat().st_size, "preview": text[:600]})
    return files


def build_file_diff(
    before_files: list[dict[str, Any]],
    after_files: list[dict[str, Any]],
    restored_files: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造执行前、执行后、复原后的文件对比。"""
    before = {item["path"]: item for item in before_files}
    after = {item["path"]: item for item in after_files}
    restored = {item["path"]: item for item in restored_files}
    paths = sorted(set(before) | set(after) | set(restored))
    rows = []
    summary = {"created": 0, "deleted": 0, "modified": 0, "unchanged": 0, "restored": 0}

    for path in paths:
        before_item = before.get(path)
        after_item = after.get(path)
        restored_item = restored.get(path)
        if before_item and not after_item:
            status = "deleted"
        elif not before_item and after_item:
            status = "created"
        elif before_item and after_item and file_signature(before_item) != file_signature(after_item):
            status = "modified"
        else:
            status = "unchanged"

        restored_ok = file_signature(before_item) == file_signature(restored_item)
        summary[status] += 1
        if restored_ok and status != "unchanged":
            summary["restored"] += 1

        rows.append(
            {
                "path": path,
                "status": status,
                "restored": restored_ok,
                "before": before_item,
                "after": after_item,
                "restored_file": restored_item,
            }
        )
    return {"files": rows, "summary": summary}


def file_signature(item: dict[str, Any] | None) -> tuple[int, str] | None:
    """返回文件对比签名。"""
    if not item:
        return None
    return int(item.get("size", 0)), str(item.get("preview", ""))


def resolve_mock_path(case_dir: Path, path_text: str) -> Path:
    """把任意路径映射到 mock 工作目录内。"""
    clean = str(path_text or "unknown").replace("\\", "/").lstrip("/")
    path = (case_dir / clean).resolve()
    root = case_dir.resolve()
    if root != path and root not in path.parents:
        raise ValueError(f"路径越界: {path_text}")
    return path


def pick_path(arguments: Any) -> str:
    """从工具参数中提取路径。"""
    if isinstance(arguments, dict):
        return str(arguments.get("path") or arguments.get("file") or arguments.get("target") or "unknown")
    return str(arguments)


def display_mock_path(case_dir: Path, path: Path) -> str:
    """展示 mock 目录相对路径。"""
    return path.resolve().relative_to(case_dir.resolve()).as_posix()


def reset_dir(path: Path) -> None:
    """重置目录。"""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def append_file(path: Path, text: str) -> None:
    """追加写入文本文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file_obj:
        file_obj.write(text)


def safe_name(value: str) -> str:
    """生成安全目录名。"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
