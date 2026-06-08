# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

This is a test framework for validating a security/protection device that detects when an LLM agent's tool calls deviate from user intent (e.g., user says "read a file" but the model tries to delete it). The framework sends OpenAI Chat Completions requests through the protection device to a built-in Mock LLM that returns controlled `tool_calls`, then asserts whether the device correctly blocks or allows each scenario.

```
Test Runner / Web Console → Protection Device (device_url) → Mock LLM (/v1/chat/completions)
```

## Commands

```bash
cd /Users/fanyunfei/Desktop/26.07/意图识别/agent_intent_test_framework
source .venv/bin/activate

# Start Django web console (recommended, combines UI + Mock LLM)
python manage.py runserver 0.0.0.0:18081

# CLI: dry-run (list cases)
python -m intent_console.runner --config config.yaml --scenarios-dir scenarios --dry-run

# CLI: run all cases
python -m intent_console.runner --config config.yaml --scenarios-dir scenarios

# CLI: run single case
python -m intent_console.runner --config config.yaml --scenarios-dir scenarios --case file_read_delete_block

# Development validation
python manage.py check
python -m py_compile intent_console/common.py intent_console/mock_llm.py intent_console/runner.py intent_console/tool_executor.py intent_console/views.py
```

## Architecture

The system has three layers, all within a single process (Django mode):

- **Web Console** (`intent_console/views.py` via Django): Browser UI at `/` for case selection, request preview, execution, and result inspection. Routes defined in `intent_console/urls.py`.
- **Mock LLM** (`intent_console/mock_llm.py`, served at `/v1/chat/completions`): Returns controlled `tool_calls` keyed by `metadata.intent_case_id` or user prompt matching. Supports both stream (SSE) and non-stream responses.
- **Runner** (`intent_console/runner.py`): Sends requests to the protection device, asserts HTTP status and block text, then delegates to `intent_console/tool_executor.py` for mock tool side effects. Used by both CLI and the web `/api/run` endpoint.

### Django project structure

```
├── manage.py                  # Django 管理入口
├── intent_test_site/          # Django 项目包
│   ├── settings.py
│   ├── urls.py                # 根路由，include intent_console.urls
│   ├── wsgi.py
│   └── asgi.py
├── intent_console/            # Django 应用（核心逻辑）
│   ├── views.py               # Web 控制台 + Mock LLM 视图
│   ├── urls.py                # 应用路由
│   ├── common.py              # YAML/JSON 加载工具
│   ├── mock_llm.py            # Mock LLM 共享逻辑
│   ├── runner.py              # 测试执行引擎
│   ├── tool_executor.py       # 沙箱化工具执行模拟器
│   └── static/favicon.ico     # 网站图标
├── templates/index.html       # Web 控制台 HTML
├── scenarios/                 # YAML 测试用例定义
└── config.example.yaml
```

### Key modules

- `intent_console/common.py`: YAML/JSON loading with optional PyYAML fallback to stdlib JSON. `load_json_compatible_yaml()` is the single entry point for reading all scenario files.
- `intent_console/tool_executor.py`: `MockToolExecutor` sandboxes all tool side effects inside `mock_workspace/<case_id>/`. Supports `read_file`, `write_file`, `delete_file`, `bash` (simulated commands: `rm`, `cat`, `ls`, `du`, `npm install`, `curl`, `chmod`, `nc`, `/dev/tcp`). All paths are confined to the workspace.
- `intent_console/mock_llm.py`: Shared logic for Mock LLM response generation (`load_scenarios`, `find_case`, `build_non_stream_response`, `build_stream_events`).
- `intent_console/runner.py`: Test execution engine (`run_case`, `build_request`, `CaseResult`, `result_to_dict`) and CLI entry point.

### Test case structure (scenarios/*.yaml)

Files use JSON-compatible YAML, loaded by `intent_console.common.load_json_compatible_yaml()`. Each file contains a `cases` array. Key fields:

- `id` / `name`: Unique identifier and display name
- `request`: OpenAI-compatible fields (`messages`, `tools`, `tool_choice`), or shorthand `user_prompt`
- `mock_response.tool_calls`: The tool calls the Mock LLM will return (controls what the protection device sees)
- `mock_workspace.files`: Files seeded into the sandbox before tool execution
- `expect.action`: `"block"` or `"pass"`
- `expect.status`: Allowed HTTP status codes (block may return 200 or 403)
- `expect.block_text`: Must appear in blocked responses; `body_not_contains` for pass cases

### Dependency notes

- Django is the web framework (>=5.2)
- `requests` is optional — `runner.py` falls back to `urllib` if not installed
- `PyYAML` is optional — `common.py` falls back to JSON parser if not installed
- `db.sqlite3`, `reports/`, and `mock_workspace/` are gitignored, generated at runtime
