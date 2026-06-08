#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""智能体意图识别测试 Runner。"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import compact_json, iter_data_files, json_dumps, load_json_compatible_yaml
from .tool_executor import MockToolExecutor

try:
    import requests  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 无依赖环境走 urllib 降级路径
    requests = None


DEFAULT_BLOCK_TEXT = "智能体执行行为与用户意图偏离，无法继续执行。"


@dataclass
class CaseResult:
    """单个测试场景的执行结果。"""

    case_id: str
    name: str
    passed: bool
    elapsed_ms: int
    status_code: int | None
    expected_action: str
    error: str
    request: dict[str, Any]
    response_text: str
    tool_effect: dict[str, Any]


def result_to_dict(result: CaseResult) -> dict[str, Any]:
    """把执行结果转换为 Web API 可返回的字典。"""
    return {
        "case_id": result.case_id,
        "name": result.name,
        "passed": result.passed,
        "elapsed_ms": result.elapsed_ms,
        "status_code": result.status_code,
        "expected_action": result.expected_action,
        "error": result.error,
        "request": result.request,
        "response_text": result.response_text,
        "tool_effect": result.tool_effect,
    }


def load_cases(scenarios_dir: str | Path) -> list[dict[str, Any]]:
    """加载所有启用的测试场景。"""
    cases: list[dict[str, Any]] = []
    for file_path in iter_data_files(scenarios_dir):
        data = load_json_compatible_yaml(file_path)
        if isinstance(data, dict) and "cases" in data:
            items = data["cases"]
        elif isinstance(data, list):
            items = data
        else:
            raise ValueError(f"{file_path} 必须是数组，或包含 cases 数组")
        for item in items:
            if item.get("enabled", True):
                cases.append(item)
    return cases


def build_tools(tool_defs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把简化工具定义转换为 OpenAI tools 结构。"""
    tools: list[dict[str, Any]] = []
    for tool in tool_defs:
        if tool.get("type") == "function" and "function" in tool:
            tools.append(tool)
            continue
        name = tool["name"]
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": tool.get("description", name),
                    "parameters": tool.get(
                        "parameters",
                        {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": True,
                        },
                    ),
                },
            }
        )
    return tools


def build_request(config: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """根据配置和场景构造 OpenAI Chat Completions 请求。"""
    request = case.get("request", {})
    messages = request.get("messages")
    if not messages:
        messages = [{"role": "user", "content": request.get("user_prompt", "")}]

    stream = case.get("stream", config.get("default_stream", False))
    body: dict[str, Any] = {
        "model": request.get("model", config.get("model", "mock-agent-intent-model")),
        "messages": messages,
        "stream": bool(stream),
        "metadata": {"intent_case_id": case["id"]},
    }

    tools = request.get("tools")
    if tools:
        body["tools"] = build_tools(tools)
    if "tool_choice" in request:
        body["tool_choice"] = request["tool_choice"]
    return body


def http_post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: int,
) -> tuple[int, str]:
    """发送 HTTP 请求，并兼容正常响应与错误响应体。"""
    # 本地 Mock 或本机防护设备调试时应绕过系统代理，避免 localhost 被代理拦截。
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    if requests is not None:
        try:
            resp = requests.post(url, json=body, headers=headers, timeout=timeout)
            return resp.status_code, resp.text
        except requests.RequestException as exc:
            raise RuntimeError(f"请求防护设备失败: {exc}") from exc

    payload = compact_json(body).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def build_headers(config: dict[str, Any]) -> dict[str, str]:
    """构造请求头。"""
    headers = dict(config.get("headers", {}))
    api_key = config.get("api_key")
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    return headers


def assert_case(
    case: dict[str, Any],
    status_code: int | None,
    response_text: str,
) -> tuple[bool, str]:
    """根据场景期望断言执行结果。"""
    expect = case.get("expect", {})
    expected_status = expect.get("status", [200])
    if isinstance(expected_status, int):
        expected_status = [expected_status]
    if status_code not in expected_status:
        return False, f"状态码不符合预期: actual={status_code}, expected={expected_status}"

    action = expect.get("action", "pass")
    block_text = expect.get("block_text", DEFAULT_BLOCK_TEXT)
    if action == "block":
        if block_text not in response_text:
            return False, f"预期拦截文案未出现: {block_text}"
    elif action == "pass":
        if block_text in response_text:
            return False, "预期放行，但响应中出现拦截文案"
    else:
        return False, f"未知 expect.action: {action}"

    contains_any = expect.get("body_contains_any", [])
    if contains_any and not any(text in response_text for text in contains_any):
        return False, f"响应未包含任一预期文本: {contains_any}"

    not_contains = expect.get("body_not_contains", [])
    for text in not_contains:
        if text in response_text:
            return False, f"响应包含不应出现的文本: {text}"

    if case.get("stream", False) and expect.get("require_done", True):
        if "[DONE]" not in response_text:
            return False, "流式响应未包含 [DONE]"

    return True, ""


def run_case(config: dict[str, Any], case: dict[str, Any]) -> CaseResult:
    """执行单个测试场景。"""
    body = build_request(config, case)
    headers = build_headers(config)
    started_at = time.time()
    status_code: int | None = None
    response_text = ""
    error = ""
    tool_effect: dict[str, Any] = {}
    try:
        status_code, response_text = http_post_json(
            config["device_url"],
            body,
            headers,
            int(config.get("timeout_seconds", 30)),
        )
        passed, error = assert_case(case, status_code, response_text)
        workspace = config.get("mock_workspace", "mock_workspace")
        tool_effect = MockToolExecutor(workspace).execute_case(case, response_text)
    except Exception as exc:  # noqa: BLE001 - 测试工具需要把异常写入报告
        passed = False
        error = f"{type(exc).__name__}: {exc}"

    elapsed_ms = int((time.time() - started_at) * 1000)
    return CaseResult(
        case_id=case["id"],
        name=case.get("name", case["id"]),
        passed=passed,
        elapsed_ms=elapsed_ms,
        status_code=status_code,
        expected_action=case.get("expect", {}).get("action", "pass"),
        error=error,
        request=body,
        response_text=response_text,
        tool_effect=tool_effect,
    )


def write_reports(results: list[CaseResult], report_dir: str | Path) -> tuple[Path, Path]:
    """写入 JSON 和 Markdown 报告。"""
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"run-{timestamp}.json"
    md_path = output_dir / "latest.md"

    json_data = [
        {
            "case_id": item.case_id,
            "name": item.name,
            "passed": item.passed,
            "elapsed_ms": item.elapsed_ms,
            "status_code": item.status_code,
            "expected_action": item.expected_action,
            "error": item.error,
            "request": item.request,
            "response_text": item.response_text,
            "tool_effect": item.tool_effect,
        }
        for item in results
    ]
    json_path.write_text(json_dumps(json_data), encoding="utf-8")

    passed_count = sum(1 for item in results if item.passed)
    lines = [
        "# 智能体意图识别测试报告",
        "",
        f"- 执行时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 通过率：{passed_count}/{len(results)}",
        "",
        "| Case | 预期 | 状态码 | 耗时(ms) | 结果 | 错误 |",
        "| --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in results:
        result_text = "通过" if item.passed else "失败"
        error = item.error.replace("\n", " ") if item.error else ""
        lines.append(
            f"| `{item.case_id}` | {item.expected_action} | {item.status_code} | "
            f"{item.elapsed_ms} | {result_text} | {error} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="执行智能体意图识别测试场景")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--scenarios-dir", default="scenarios", help="场景目录")
    parser.add_argument("--case", help="只执行指定 case id")
    parser.add_argument("--dry-run", action="store_true", help="只列出场景，不发送请求")
    parser.add_argument("--report-dir", default="reports", help="报告目录")
    args = parser.parse_args()

    config = load_json_compatible_yaml(args.config)
    cases = load_cases(args.scenarios_dir)
    if args.case:
        cases = [case for case in cases if case.get("id") == args.case]
        if not cases:
            raise SystemExit(f"未找到 case: {args.case}")

    if args.dry_run:
        for case in cases:
            print(f"{case['id']}: {case.get('name', case['id'])}")
        print(f"共 {len(cases)} 个场景")
        return

    results = [run_case(config, case) for case in cases]
    json_path, md_path = write_reports(results, args.report_dir)
    passed_count = sum(1 for item in results if item.passed)
    print(f"执行完成，通过 {passed_count}/{len(results)}")
    print(f"JSON 报告: {json_path}")
    print(f"Markdown 报告: {md_path}")
    if passed_count != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
