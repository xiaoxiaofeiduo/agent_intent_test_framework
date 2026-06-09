"""views.py 单元测试。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from ..mock_llm import load_scenarios
from ..views import (
    STATE,
    DjangoWebState,
    mock_endpoint,
    normalize_frontend_case,
    read_json_body,
)


class DjangoWebStateTests(SimpleTestCase):
    """测试 Django Web 状态管理。"""

    def test_state_initializes(self) -> None:
        self.assertIsNotNone(STATE.base_scenarios)
        self.assertIsInstance(STATE.base_scenarios, dict)
        # 应包含至少一个用例
        self.assertGreater(len(STATE.base_scenarios), 0)

    def test_reset_active_scenarios(self) -> None:
        original_count = len(STATE.base_scenarios)
        STATE.reset_active_scenarios([
            {"id": "case_read", "name": "overridden", "mock_response": {"content": "custom"}}
        ])
        active = STATE.get_active_scenarios()
        self.assertIn("case_read", active)
        self.assertEqual(active["case_read"]["name"], "overridden")


class NormalizeFrontendCaseTests(SimpleTestCase):
    """测试前端用例规范化。"""

    def test_fills_defaults(self) -> None:
        normalized = normalize_frontend_case(
            {"id": "new_case"},
            fallback_id="new_case",
        )
        self.assertEqual(normalized["id"], "new_case")
        self.assertEqual(normalized["name"], "new_case")
        self.assertIn("request", normalized)
        self.assertIn("expect", normalized)
        self.assertIn("mock_response", normalized)

    def test_preserves_existing_fields(self) -> None:
        case = {"id": "c1", "name": "my case", "stream": True}
        normalized = normalize_frontend_case(case, fallback_id="c1")
        self.assertEqual(normalized["name"], "my case")

    def test_inherits_mock_workspace_from_fallback(self) -> None:
        normalized = normalize_frontend_case(
            {"id": "c1"},
            fallback_id="c1",
            fallback_case={"mock_workspace": {"files": {"test.txt": "data"}}},
        )
        self.assertIn("mock_workspace", normalized)
        self.assertEqual(normalized["mock_workspace"]["files"]["test.txt"], "data")

    def test_non_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_frontend_case("not a dict", fallback_id="bad")  # type: ignore[arg-type]


class ReadJsonBodyTests(SimpleTestCase):
    """测试 JSON 请求体读取。"""

    def test_empty_body_returns_empty_dict(self) -> None:
        from django.test import RequestFactory
        request = RequestFactory().post("/api/test", content_type="application/json")
        result = read_json_body(request)
        self.assertEqual(result, {})

    def test_parses_json_body(self) -> None:
        from django.test import RequestFactory
        request = RequestFactory().post(
            "/api/test",
            data=json.dumps({"key": "value"}),
            content_type="application/json",
        )
        result = read_json_body(request)
        self.assertEqual(result, {"key": "value"})


class MockEndpointTests(SimpleTestCase):
    """测试 Mock 端点 URL 构造。"""

    def test_returns_absolute_uri(self) -> None:
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        url = mock_endpoint(request)
        self.assertTrue(url.endswith("/v1/chat/completions"))


class ViewEndpointTests(TestCase):
    """测试视图 HTTP 端点。"""

    def test_index_returns_html(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<!doctype html>')
        self.assertContains(response, 'id="originUrl"')
        self.assertContains(response, 'value="http://10.10.121.15:18081/v1/chat/completions"')
        self.assertContains(response, 'id="originApiKey"')
        self.assertContains(response, 'id="originHeadersText"')
        self.assertContains(response, 'id="exportHtmlBtn"')
        self.assertContains(response, 'id="exportJsonBtn"')

    def test_healthz_returns_ok(self) -> None:
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "ok")

    def test_favicon_returns_image(self) -> None:
        response = self.client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/x-icon")

    def test_cases_api_returns_data(self) -> None:
        response = self.client.get("/api/cases")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("mock_endpoint", data)
        self.assertIn("cases", data)
        self.assertIsInstance(data["cases"], list)
        self.assertGreater(len(data["cases"]), 0)

    def test_cases_api_rejects_non_get(self) -> None:
        response = self.client.post("/api/cases")
        self.assertEqual(response.status_code, 405)

    def test_preview_requires_post(self) -> None:
        response = self.client.get("/api/preview")
        self.assertEqual(response.status_code, 405)

    def test_preview_returns_request_preview(self) -> None:
        case = {
            "id": "preview_test",
            "request": {"user_prompt": "测试"},
            "mock_response": {},
            "expect": {"action": "pass"},
        }
        response = self.client.post(
            "/api/preview",
            data=json.dumps({"case": case}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("request", data)

    def test_run_requires_post(self) -> None:
        response = self.client.get("/api/run")
        self.assertEqual(response.status_code, 405)

    def test_run_requires_case_ids(self) -> None:
        response = self.client.post(
            "/api/run",
            data=json.dumps({"case_ids": [], "device_url": "http://localhost/test"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("至少选择一个用例", data["error"])

    def test_report_download_returns_report_file(self) -> None:
        report_dir = Path(settings.REPORT_DIR)
        report_dir.mkdir(parents=True, exist_ok=True)
        report_path = report_dir / "test-report.md"
        report_path.write_text("# report\n", encoding="utf-8")

        response = self.client.get("/api/reports/test-report.md")

        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        content = b"".join(response.streaming_content).decode()
        self.assertEqual(content, "# report\n")

    def test_report_download_rejects_path_traversal(self) -> None:
        response = self.client.get("/api/reports/../config.example.yaml")
        self.assertEqual(response.status_code, 400)

    def test_mock_llm_non_stream(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            data=json.dumps({
                "model": "test",
                "messages": [{"role": "user", "content": "帮我读文件"}],
                "metadata": {"intent_case_id": "file_read_allow"},
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("choices", data)

    def test_mock_llm_stream(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            data=json.dumps({
                "model": "test",
                "stream": True,
                "messages": [{"role": "user", "content": "帮我读文件"}],
                "metadata": {"intent_case_id": "file_read_allow"},
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response["Content-Type"])
        content = b"".join(response.streaming_content).decode()
        self.assertIn("data: ", content)
        self.assertIn("[DONE]", content)

    def test_mock_llm_requires_post(self) -> None:
        response = self.client.get("/v1/chat/completions")
        self.assertEqual(response.status_code, 405)
