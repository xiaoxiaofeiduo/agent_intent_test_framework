# -*- coding: utf-8 -*-

"""Django Web 控制台和 Mock LLM 视图。"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from django.conf import settings
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

from .common import compact_json
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
        items.append(
            {
                "id": case["id"],
                "name": case.get("name", case["id"]),
                "stream": bool(case.get("stream", False)),
                "user_prompt": request_data.get("user_prompt", ""),
                "expect_action": expect.get("action", "pass"),
                "case": case,
                "mock_response": case.get("mock_response", {}),
            }
        )
    return JsonResponse({"mock_endpoint": mock_endpoint(request), "cases": items})


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
