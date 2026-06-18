#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""智能体意图识别测试 Runner。"""

from __future__ import annotations

import argparse
import html
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
    origin_status_code: int | None = None
    origin_elapsed_ms: int = 0
    origin_response_text: str = ""
    origin_error: str = ""


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
        "origin_status_code": result.origin_status_code,
        "origin_elapsed_ms": result.origin_elapsed_ms,
        "origin_response_text": result.origin_response_text,
        "origin_error": result.origin_error,
        "comparison": build_response_comparison(result),
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
    if config.get("mock_protection"):
        body["metadata"]["intent_mock_protection"] = True

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
    retries: int = 1,
    retry_delay_seconds: float = 1.0,
) -> tuple[int, str]:
    """发送 HTTP 请求，并兼容正常响应与错误响应体。

    支持重试：配置中 retries 默认 1 表示最多重试 1 次（共 2 次请求）。
    仅对连接超时等网络错误重试，HTTP 4xx/5xx 视为有效响应不重试。
    """
    # 本地 Mock 或本机防护设备调试时应绕过系统代理，避免 localhost 被代理拦截。
    os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
    os.environ.setdefault("no_proxy", "127.0.0.1,localhost")

    last_error: str = ""

    for attempt in range(retries + 1):
        try:
            if requests is not None:
                resp = requests.post(url, json=body, headers=headers, timeout=timeout)
                return resp.status_code, resp.text

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

        except (OSError, TimeoutError) as exc:
            # requests wraps network errors as RequestException，OSError/TimeoutError 覆盖 urllib 路径
            last_error = str(exc)
            if attempt < retries:
                time.sleep(retry_delay_seconds * (attempt + 1))
        except Exception as exc:
            # requests 路径的异常
            last_error = str(exc)
            if attempt < retries and "timeout" in str(exc).lower():
                time.sleep(retry_delay_seconds * (attempt + 1))
            elif attempt >= retries:
                raise RuntimeError(f"请求防护设备失败: {exc}") from exc

    raise RuntimeError(f"请求防护设备失败（已重试 {retries} 次）: {last_error}")


def build_headers(config: dict[str, Any]) -> dict[str, str]:
    """构造请求头。"""
    headers = dict(config.get("headers", {}))
    api_key = config.get("api_key")
    if api_key:
        headers.setdefault("Authorization", f"Bearer {api_key}")
    return headers


def build_origin_headers(config: dict[str, Any]) -> dict[str, str]:
    """构造原站请求头，默认复用防护侧请求头。"""
    headers = build_headers(config)
    origin_headers = config.get("origin_headers")
    if isinstance(origin_headers, dict):
        headers.update(origin_headers)
    origin_api_key = config.get("origin_api_key")
    if origin_api_key:
        headers["Authorization"] = f"Bearer {origin_api_key}"
    return headers


def build_response_comparison(result: CaseResult) -> dict[str, Any]:
    """生成原站与防护响应的轻量对比信息。"""
    protected_blocked = DEFAULT_BLOCK_TEXT in result.response_text
    origin_blocked = DEFAULT_BLOCK_TEXT in result.origin_response_text
    return {
        "enabled": bool(result.origin_response_text or result.origin_error or result.origin_status_code),
        "status_changed": result.origin_status_code != result.status_code,
        "protected_blocked": protected_blocked,
        "origin_blocked": origin_blocked,
        "block_changed": origin_blocked != protected_blocked,
        "protected_response_bytes": len(result.response_text.encode("utf-8")),
        "origin_response_bytes": len(result.origin_response_text.encode("utf-8")),
    }


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
    status_code: int | None = None
    response_text = ""
    error = ""
    tool_effect: dict[str, Any] = {}
    origin_status_code: int | None = None
    origin_elapsed_ms = 0
    origin_response_text = ""
    origin_error = ""
    origin_url = str(config.get("origin_url") or "").strip()
    try:
        if origin_url:
            origin_started_at = time.time()
            try:
                origin_status_code, origin_response_text = http_post_json(
                    origin_url,
                    body,
                    build_origin_headers(config),
                    int(config.get("timeout_seconds", 30)),
                )
            except Exception as exc:  # noqa: BLE001 - 原站对比失败不应阻断防护侧断言
                origin_error = f"{type(exc).__name__}: {exc}"
            origin_elapsed_ms = int((time.time() - origin_started_at) * 1000)

        started_at = time.time()
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

    elapsed_ms = int((time.time() - started_at) * 1000) if "started_at" in locals() else 0
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
        origin_status_code=origin_status_code,
        origin_elapsed_ms=origin_elapsed_ms,
        origin_response_text=origin_response_text,
        origin_error=origin_error,
    )


def html_escape(value: Any) -> str:
    """转义 HTML 文本。"""
    return html.escape(str(value), quote=True)


def format_report_json(value: Any) -> str:
    """格式化报告中的 JSON 数据。"""
    return html_escape(json_dumps(value))


def render_html_report(results: list[CaseResult], executed_at: str) -> str:
    """生成包含完整请求、响应和工具效果的 HTML 报告。"""
    passed_count = sum(1 for item in results if item.passed)
    rows: list[str] = []
    detail_sections: list[str] = []
    for item in results:
        result_text = "通过" if item.passed else "失败"
        result_class = "pass" if item.passed else "fail"
        comparison = build_response_comparison(item)
        tool_effect = item.tool_effect or {}
        rows.append(
            "<tr>"
            f"<td><a href=\"#{html_escape(item.case_id)}\">{html_escape(item.case_id)}</a></td>"
            f"<td>{html_escape(item.name)}</td>"
            f"<td>{html_escape(item.expected_action)}</td>"
            f"<td>{html_escape(item.origin_status_code if item.origin_status_code is not None else '-')}</td>"
            f"<td>{html_escape(item.status_code if item.status_code is not None else '-')}</td>"
            f"<td>{html_escape(item.elapsed_ms)} ms</td>"
            f"<td class=\"{result_class}\">{result_text}</td>"
            f"<td>{html_escape(item.error or '')}</td>"
            "</tr>"
        )
        detail_sections.append(
            f"""
            <section class="case-detail" id="{html_escape(item.case_id)}">
              <h2>{html_escape(item.case_id)} <span class="{result_class}">{result_text}</span></h2>
              <div class="grid">
                <div>
                  <h3>基础信息</h3>
                  <dl>
                    <dt>名称</dt><dd>{html_escape(item.name)}</dd>
                    <dt>预期动作</dt><dd>{html_escape(item.expected_action)}</dd>
                    <dt>防护状态码</dt><dd>{html_escape(item.status_code if item.status_code is not None else "-")}</dd>
                    <dt>防护耗时</dt><dd>{html_escape(item.elapsed_ms)} ms</dd>
                    <dt>断言错误</dt><dd>{html_escape(item.error or "-")}</dd>
                  </dl>
                </div>
                <div>
                  <h3>原站与防护对比</h3>
                  <dl>
                    <dt>原站状态码</dt><dd>{html_escape(item.origin_status_code if item.origin_status_code is not None else "-")}</dd>
                    <dt>原站耗时</dt><dd>{html_escape(item.origin_elapsed_ms)} ms</dd>
                    <dt>状态码变化</dt><dd>{html_escape("是" if comparison["status_changed"] else "否")}</dd>
                    <dt>拦截变化</dt><dd>{html_escape("是" if comparison["block_changed"] else "否")}</dd>
                    <dt>原站字节</dt><dd>{html_escape(comparison["origin_response_bytes"])}</dd>
                    <dt>防护字节</dt><dd>{html_escape(comparison["protected_response_bytes"])}</dd>
                    <dt>原站错误</dt><dd>{html_escape(item.origin_error or "-")}</dd>
                  </dl>
                </div>
              </div>
              <details open><summary>完整请求</summary><pre>{format_report_json(item.request)}</pre></details>
              <details open><summary>原站完整响应</summary><pre>{html_escape(item.origin_response_text or item.origin_error or "")}</pre></details>
              <details open><summary>防护完整响应</summary><pre>{html_escape(item.response_text)}</pre></details>
              <details><summary>工具执行效果</summary><pre>{format_report_json(tool_effect)}</pre></details>
              <details><summary>机器可读完整结果</summary><pre>{format_report_json(result_to_dict(item))}</pre></details>
            </section>
            """
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>智能体意图识别测试报告</title>
  <style>
    body {{ margin:0; padding:24px; background:#f6f8fb; color:#111827; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    h1 {{ margin:0 0 8px; font-size:24px; }}
    h2 {{ margin:0 0 12px; font-size:18px; }}
    h3 {{ margin:0 0 8px; font-size:15px; }}
    .meta {{ color:#64748b; margin-bottom:18px; }}
    .summary, .case-detail {{ background:#fff; border:1px solid #d8dee8; border-radius:8px; padding:16px; margin-bottom:16px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ border-bottom:1px solid #e5e7eb; padding:8px; text-align:left; vertical-align:top; }}
    th {{ background:#f8fafc; color:#475569; }}
    a {{ color:#2563eb; text-decoration:none; }}
    .pass {{ color:#15803d; font-weight:600; }}
    .fail {{ color:#b91c1c; font-weight:600; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
    dl {{ display:grid; grid-template-columns:112px minmax(0,1fr); gap:6px 10px; margin:0; }}
    dt {{ color:#64748b; }}
    dd {{ margin:0; word-break:break-word; }}
    details {{ margin-top:12px; }}
    summary {{ cursor:pointer; color:#2563eb; }}
    pre {{ background:#0f172a; color:#dbeafe; border-radius:6px; padding:12px; overflow:auto; max-height:520px; white-space:pre-wrap; overflow-wrap:anywhere; }}
  </style>
</head>
<body>
  <h1>智能体意图识别测试报告</h1>
  <div class="meta">执行时间：{html_escape(executed_at)} | 通过率：{html_escape(passed_count)}/{html_escape(len(results))}</div>
  <section class="summary">
    <h2>结果汇总</h2>
    <table>
      <thead><tr><th>Case</th><th>名称</th><th>预期</th><th>原站状态码</th><th>防护状态码</th><th>耗时</th><th>结果</th><th>错误</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </section>
  {"".join(detail_sections)}
</body>
</html>
"""


def write_reports(results: list[CaseResult], report_dir: str | Path) -> tuple[Path, Path, Path]:
    """写入 JSON、Markdown 和 HTML 报告。"""
    output_dir = Path(report_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    executed_at = datetime.now().isoformat(timespec="seconds")
    json_path = output_dir / f"run-{timestamp}.json"
    html_path = output_dir / f"run-{timestamp}.html"
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
            "origin_status_code": item.origin_status_code,
            "origin_elapsed_ms": item.origin_elapsed_ms,
            "origin_response_text": item.origin_response_text,
            "origin_error": item.origin_error,
            "comparison": build_response_comparison(item),
        }
        for item in results
    ]
    json_path.write_text(json_dumps(json_data), encoding="utf-8")
    html_path.write_text(render_html_report(results, executed_at), encoding="utf-8")

    passed_count = sum(1 for item in results if item.passed)
    lines = [
        "# 智能体意图识别测试报告",
        "",
        f"- 执行时间：{executed_at}",
        f"- 通过率：{passed_count}/{len(results)}",
        "",
        "| Case | 预期 | 原站状态码 | 防护状态码 | 防护耗时(ms) | 结果 | 错误 |",
        "| --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for item in results:
        result_text = "通过" if item.passed else "失败"
        error = item.error.replace("\n", " ") if item.error else ""
        lines.append(
            f"| `{item.case_id}` | {item.expected_action} | {item.origin_status_code} | {item.status_code} | "
            f"{item.elapsed_ms} | {result_text} | {error} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path, html_path


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="执行智能体意图识别测试场景")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--scenarios-dir", default="scenarios", help="场景目录")
    parser.add_argument("--case", help="只执行指定 case id")
    parser.add_argument("--dry-run", action="store_true", help="只列出场景，不发送请求")
    parser.add_argument("--report-dir", default="reports", help="报告目录")
    parser.add_argument("--origin-url", help="不经过防护设备的原站 Chat Completions 地址，用于响应对比")
    args = parser.parse_args()

    config = load_json_compatible_yaml(args.config)
    if args.origin_url:
        config["origin_url"] = args.origin_url
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
    json_path, md_path, html_path = write_reports(results, args.report_dir)
    passed_count = sum(1 for item in results if item.passed)
    print(f"执行完成，通过 {passed_count}/{len(results)}")
    print(f"JSON 报告: {json_path}")
    print(f"Markdown 报告: {md_path}")
    print(f"HTML 报告: {html_path}")
    if passed_count != len(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
