# -*- coding: utf-8 -*-

"""Django Web 控制台和 Mock LLM 视图。"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .common import compact_json, iter_data_files, load_json_compatible_yaml
from .mock_llm import build_non_stream_response, build_stream_events, find_case, load_scenarios
from .runner import build_request, run_case, write_reports, result_to_dict


DEFAULT_ORIGIN_URL = "http://10.10.121.15:18081/v1/chat/completions"


class DjangoWebState:
    """保存 Django 运行态。"""

    def __init__(self) -> None:
        self.scenarios_dir = Path(settings.SCENARIOS_DIR)
        self.report_dir = Path(settings.REPORT_DIR)
        self.mock_workspace = Path(settings.MOCK_WORKSPACE)
        self.lock = threading.Lock()
        self.base_scenarios = load_scenarios(self.scenarios_dir)
        self.scenario_types = load_scenario_types(self.scenarios_dir)
        self.active_scenarios = copy.deepcopy(self.base_scenarios)

    def reset_active_scenarios(self, cases: list[dict[str, Any]]) -> None:
        """根据本次执行用例刷新 Mock LLM 活动场景。"""
        with self.lock:
            scenarios = copy.deepcopy(self.base_scenarios)
            for case in cases:
                scenarios[case["id"]] = copy.deepcopy(case)
            self.active_scenarios = scenarios

    def get_active_scenarios(self) -> dict[str, dict[str, Any]]:
        """读取当前活动场景。"""
        with self.lock:
            return self.active_scenarios


def load_scenario_types(scenarios_dir: str | Path) -> dict[str, str]:
    """按 YAML 文件名建立 case id 到场景类型的映射。"""
    case_types: dict[str, str] = {}
    for file_path in iter_data_files(scenarios_dir):
        data = load_json_compatible_yaml(file_path)
        if isinstance(data, dict) and "cases" in data:
            case_list = data["cases"]
        elif isinstance(data, list):
            case_list = data
        else:
            continue
        for case in case_list:
            case_id = case.get("id") if isinstance(case, dict) else None
            if case_id:
                case_types[case_id] = file_path.stem
    return case_types


STATE = DjangoWebState()


def index(request: HttpRequest) -> HttpResponse:
    """返回 Web 控制台页面。"""
    return render(request, "index.html")


def favicon(_request: HttpRequest) -> FileResponse:
    """返回 favicon。"""
    path = Path(__file__).parent / "static" / "favicon.ico"
    return FileResponse(open(path, "rb"), content_type="image/x-icon")


def healthz(_request: HttpRequest) -> HttpResponse:
    """健康检查。"""
    return HttpResponse("ok", content_type="text/plain; charset=utf-8")


def report_download(request: HttpRequest, filename: str) -> FileResponse | JsonResponse:
    """下载 reports 目录下的测试报告。"""
    if request.method != "GET":
        return JsonResponse({"error": "method not allowed"}, status=405)
    if Path(filename).name != filename or not filename.endswith((".json", ".md", ".html")):
        return JsonResponse({"error": "invalid report filename"}, status=400)
    path = (STATE.report_dir / filename).resolve()
    report_dir = STATE.report_dir.resolve()
    if report_dir not in path.parents or not path.exists() or not path.is_file():
        return JsonResponse({"error": "report not found"}, status=404)
    return FileResponse(open(path, "rb"), as_attachment=True, filename=filename)


def cases(request: HttpRequest) -> JsonResponse:
    """返回前端用例列表。"""
    if request.method != "GET":
        return JsonResponse({"error": "method not allowed"}, status=405)
    items = []
    for case in STATE.base_scenarios.values():
        request_data = case.get("request", {})
        expect = case.get("expect", {})
        case_type = STATE.scenario_types.get(case["id"], "unknown")
        items.append(
            {
                "id": case["id"],
                "name": case.get("name", case["id"]),
                "case_type": case_type,
                "case_type_label": case_type.replace("_", " "),
                "stream": bool(case.get("stream", False)),
                "user_prompt": request_data.get("user_prompt", ""),
                "expect_action": expect.get("action", "pass"),
                "case": case,
                "mock_response": case.get("mock_response", {}),
            }
        )
    case_types = sorted({item["case_type"] for item in items})
    return JsonResponse({"mock_endpoint": mock_endpoint(request), "case_types": case_types, "cases": items})


@csrf_exempt
def preview(request: HttpRequest) -> JsonResponse:
    """预览前端编辑后的 OpenAI 请求体。"""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        body = read_json_body(request)
        case = normalize_frontend_case(body.get("case", {}), fallback_id="preview")
        config = {"model": "mock-agent-intent-model", "default_stream": False}
        return JsonResponse({"request": build_request(config, case)})
    except Exception as exc:  # noqa: BLE001 - Web API 需要把错误返回给页面
        return JsonResponse({"error": f"{type(exc).__name__}: {exc}"}, status=400)


@csrf_exempt
def run_cases(request: HttpRequest) -> JsonResponse:
    """执行前端选择的测试用例。"""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        body = read_json_body(request)
        case_ids = body.get("case_ids") or []
        if not case_ids:
            return JsonResponse({"ok": False, "error": "请至少选择一个用例", "results": []}, status=400)

        selected_cases: list[dict[str, Any]] = []
        overrides = body.get("case_overrides") if isinstance(body.get("case_overrides"), dict) else {}
        for case_id in case_ids:
            if case_id not in STATE.base_scenarios:
                raise ValueError(f"未知用例: {case_id}")
            case = copy.deepcopy(STATE.base_scenarios[case_id])
            if case_id in overrides and overrides[case_id]:
                case = normalize_frontend_case(overrides[case_id], fallback_id=case_id, fallback_case=case)
            selected_cases.append(case)

        STATE.reset_active_scenarios(selected_cases)

        config = {
            "device_url": body.get("device_url", ""),
            "origin_url": body.get("origin_url") or DEFAULT_ORIGIN_URL,
            "api_key": body.get("api_key", ""),
            "origin_api_key": body.get("origin_api_key", ""),
            "headers": body.get("headers", {}) if isinstance(body.get("headers"), dict) else {},
            "origin_headers": body.get("origin_headers", {}) if isinstance(body.get("origin_headers"), dict) else {},
            "timeout_seconds": int(body.get("timeout_seconds") or 30),
            "model": "mock-agent-intent-model",
            "mock_workspace": str(STATE.mock_workspace),
        }
        results = [run_case(config, case) for case in selected_cases]
        json_path, md_path, html_path = write_reports(results, STATE.report_dir)
        payload = {
            "ok": all(item.passed for item in results),
            "passed": sum(1 for item in results if item.passed),
            "total": len(results),
            "report_json": str(json_path),
            "report_md": str(md_path),
            "report_html": str(html_path),
            "report_json_url": f"/api/reports/{quote(json_path.name)}",
            "report_md_url": f"/api/reports/{quote(md_path.name)}",
            "report_html_url": f"/api/reports/{quote(html_path.name)}",
            "results": [result_to_dict(item) for item in results],
        }
        return JsonResponse(payload)
    except Exception as exc:  # noqa: BLE001 - Web API 需要把错误返回给页面
        return JsonResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}", "results": []}, status=500)


@csrf_exempt
def automation_run_case(request: HttpRequest) -> JsonResponse:
    """自动化入口：按目标地址执行指定用例。"""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        body = read_json_body(request)
        case_id = str(body.get("case_id") or "").strip()
        device_url = str(body.get("device_url") or body.get("target_url") or body.get("protected_url") or "").strip()
        if not case_id:
            return JsonResponse({"ok": False, "error": "case_id is required"}, status=400)
        if not device_url:
            return JsonResponse({"ok": False, "error": "device_url is required"}, status=400)
        if case_id not in STATE.base_scenarios:
            return JsonResponse({"ok": False, "error": f"unknown case_id: {case_id}"}, status=404)

        case = copy.deepcopy(STATE.base_scenarios[case_id])
        STATE.reset_active_scenarios([case])
        config = {
            "device_url": device_url,
            "origin_url": body.get("origin_url") or "",
            "api_key": body.get("api_key", ""),
            "origin_api_key": body.get("origin_api_key", ""),
            "headers": body.get("headers", {}) if isinstance(body.get("headers"), dict) else {},
            "origin_headers": body.get("origin_headers", {}) if isinstance(body.get("origin_headers"), dict) else {},
            "timeout_seconds": int(body.get("timeout_seconds") or 30),
            "model": body.get("model") or "mock-agent-intent-model",
            "mock_workspace": str(STATE.mock_workspace),
        }
        result = run_case(config, case)
        json_path, md_path, html_path = write_reports([result], STATE.report_dir)
        return JsonResponse({
            "ok": result.passed,
            "case_id": case_id,
            "target_url": device_url,
            "passed": result.passed,
            "report_json": str(json_path),
            "report_md": str(md_path),
            "report_html": str(html_path),
            "report_json_url": f"/api/reports/{quote(json_path.name)}",
            "report_md_url": f"/api/reports/{quote(md_path.name)}",
            "report_html_url": f"/api/reports/{quote(html_path.name)}",
            "result": result_to_dict(result),
        })
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 - 自动化接口需要返回结构化错误
        return JsonResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)


@csrf_exempt
def reload_scenarios(request: HttpRequest) -> JsonResponse:
    """热点重载场景：无需重启服务即可使新增/修改的 YAML 生效。

    同时扫描 scenarios/ 目录，检测新增文件并自动载入。
    """
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed，请使用 POST"}, status=405)
    try:
        before_count = len(STATE.base_scenarios)
        before_ids = set(STATE.base_scenarios.keys())

        STATE.base_scenarios = load_scenarios(STATE.scenarios_dir)
        STATE.scenario_types = load_scenario_types(STATE.scenarios_dir)
        with STATE.lock:
            STATE.active_scenarios = copy.deepcopy(STATE.base_scenarios)

        after_count = len(STATE.base_scenarios)
        after_ids = set(STATE.base_scenarios.keys())
        added = sorted(after_ids - before_ids)
        removed = sorted(before_ids - after_ids)

        return JsonResponse({
            "ok": True,
            "message": f"场景已重载。变更: +{len(added)}/-{len(removed)}，共 {after_count} 个场景。",
            "before_count": before_count,
            "after_count": after_count,
            "added": added,
            "removed": removed,
            "reloaded_at": datetime.now().isoformat(timespec="seconds"),
        })
    except Exception as exc:  # noqa: BLE001 - Web API 需要把错误返回给页面
        return JsonResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, status=500)


def validate_scenarios(request: HttpRequest) -> JsonResponse:
    """校验 scenarios/ 目录下所有 YAML 文件的结构完整性。

    返回每个文件的校验结果，包括 case 数量、必填字段检查、id 唯一性等。
    """
    if request.method != "GET":
        return JsonResponse({"error": "method not allowed"}, status=405)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    total_cases = 0

    for file_path in iter_data_files(STATE.scenarios_dir):
        file_status = {"file": file_path.name, "ok": True, "case_count": 0, "issues": []}
        try:
            data = load_json_compatible_yaml(file_path)
            cases = data.get("cases") if isinstance(data, dict) else data
            if not isinstance(cases, list):
                file_status["ok"] = False
                file_status["issues"].append("顶层结构必须包含 cases 数组或本身为数组")
                results.append(file_status)
                continue

            file_status["case_count"] = len(cases)
            total_cases += len(cases)
            seen_ids: set[str] = set()
            for idx, case in enumerate(cases):
                if not isinstance(case, dict):
                    file_status["issues"].append(f"第 {idx+1} 个 case 不是对象")
                    file_status["ok"] = False
                    continue
                case_id = case.get("id")
                if not case_id:
                    file_status["issues"].append(f"第 {idx+1} 个 case 缺少 id")
                    file_status["ok"] = False
                elif case_id in seen_ids:
                    file_status["issues"].append(f"case id 重复: {case_id}")
                    file_status["ok"] = False
                else:
                    seen_ids.add(case_id)
                req = case.get("request") if isinstance(case.get("request"), dict) else {}
                if not req.get("messages") and not req.get("user_prompt"):
                    file_status["issues"].append(f"{case_id}: request.messages 和 request.user_prompt 均缺失")
                expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
                if not expect:
                    file_status["issues"].append(f"{case_id}: 缺少 expect 断言配置")
            results.append(file_status)
            if not file_status["ok"]:
                errors.append(file_status)
        except Exception as exc:  # noqa: BLE001 - 校验工具需容错
            results.append({"file": file_path.name, "ok": False, "case_count": 0, "issues": [f"加载失败: {exc}"]})
            errors.append({"file": file_path.name, "error": str(exc)})

    return JsonResponse({
        "ok": len(errors) == 0,
        "total_files": len(results),
        "total_cases": total_cases,
        "error_count": len(errors),
        "results": results,
    })


@csrf_exempt
def mock_llm(request: HttpRequest) -> HttpResponse:
    """OpenAI-compatible Mock LLM 接口。"""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        body = read_json_body(request)
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid json"}, status=400)

    scenarios = STATE.get_active_scenarios()
    case = find_case(body, scenarios)
    delay_ms = int(case.get("mock_response", {}).get("delay_ms", 0) or 0)
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)

    if body.get("stream"):
        response = StreamingHttpResponse(stream_mock_events(body, case), content_type="text/event-stream; charset=utf-8")
        response["Cache-Control"] = "no-cache"
        return response
    return JsonResponse(build_non_stream_response(body, case))


def stream_mock_events(body: dict[str, Any], case: dict[str, Any]):
    """生成 SSE Mock LLM 响应。"""
    for event in build_stream_events(body, case):
        yield f"data: {compact_json(event)}\n\n"
    yield "data: [DONE]\n\n"


def read_json_body(request: HttpRequest) -> dict[str, Any]:
    """读取 JSON 请求体。"""
    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


def normalize_frontend_case(
    case: dict[str, Any],
    fallback_id: str,
    fallback_case: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """校验并补齐前端传回的用例。"""
    if not isinstance(case, dict):
        raise ValueError("用例必须是 JSON 对象")
    normalized = copy.deepcopy(case)
    fallback_case = fallback_case or {}
    normalized.setdefault("id", fallback_id)
    normalized.setdefault("name", normalized["id"])
    normalized.setdefault("request", {})
    normalized.setdefault("mock_response", {})
    normalized.setdefault("expect", {"action": "pass", "status": [200]})
    if "mock_workspace" not in normalized and fallback_case.get("mock_workspace"):
        normalized["mock_workspace"] = copy.deepcopy(fallback_case["mock_workspace"])
    if not normalized["request"].get("messages") and "user_prompt" not in normalized["request"]:
        normalized["request"]["user_prompt"] = ""
    return normalized


def mock_endpoint(request: HttpRequest) -> str:
    """根据当前请求构造 Mock LLM 上游地址。"""
    return request.build_absolute_uri("/v1/chat/completions")
