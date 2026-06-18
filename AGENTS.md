# Repository Guidelines

## Project Structure & Module Organization

This repository is a Django-based agent intent testing framework. Core application code lives in `intent_console/`: `views.py` provides the web console and Mock LLM endpoints, `runner.py` drives scenario execution, `mock_llm.py` builds OpenAI-compatible mock responses, and `tool_executor.py` simulates tool side effects. Django project settings and root routing are in `intent_test_site/`. Test scenarios are YAML files under `scenarios/`. Unit tests are in `intent_console/tests/`. UI templates and static assets are in `templates/` and `intent_console/static/`. Runtime outputs such as `reports/`, `mock_workspace/`, `.venv/`, and `db.sqlite3` should not be treated as source changes.

## Build, Test, and Development Commands

Set up the local environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the web console and Mock LLM:

```bash
python manage.py runserver 0.0.0.0:18081
```

Run all tests:

```bash
python manage.py test
```

Run the CLI scenario runner:

```bash
python -m intent_console.runner --config config.example.yaml --scenarios-dir scenarios --dry-run
```

## Coding Style & Naming Conventions

Use Python 3 syntax, four-space indentation, type hints where useful, and concise docstrings for public helpers. Keep function and variable names in `snake_case`; class names use `PascalCase`. Scenario IDs should be descriptive, lowercase, and underscore-separated, for example `file_lifecycle_read_then_delete_block`. Prefer structured YAML/JSON handling over string parsing.

## Testing Guidelines

Tests use Django’s built-in test runner and `SimpleTestCase`/`TestCase`. Add focused tests under `intent_console/tests/` when changing runner behavior, views, scenario parsing, or mock tool execution. Name test methods by expected behavior, for example `test_automation_run_requires_case_id`. Before submitting, run `python manage.py test`.

## Commit & Pull Request Guidelines

Recent commits use short, direct Chinese summaries such as `修复CI`, `优化控制台`, and `add missing automation api cases`. Keep commits focused on one logical change. Pull requests should describe the behavior changed, list test commands run, mention affected scenario IDs or endpoints, and include screenshots only for UI changes.

## Security & Configuration Tips

Do not commit API keys, local `config.yaml`, generated reports, or mock workspace contents. `device_url` should normally point to the protected service entry, while `/v1/chat/completions` is this project’s Mock LLM endpoint.
