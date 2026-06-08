#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""智能体意图识别测试框架的公共工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - 无依赖环境走标准库降级路径
    yaml = None


def load_json_compatible_yaml(path: str | Path) -> Any:
    """加载 YAML 文件。

    如果虚拟环境安装了 PyYAML，则支持普通 YAML；如果没有安装，
    则降级为 JSON-compatible YAML，避免测试框架在设备环境无法启动。
    """
    file_path = Path(path)
    content = file_path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(content)

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{file_path} 不是有效的 JSON-compatible YAML。"
            "如需使用普通 YAML，请先在虚拟环境中安装 PyYAML。"
        ) from exc


def iter_data_files(directory: str | Path) -> list[Path]:
    """列出目录下支持的数据文件。"""
    root = Path(directory)
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {root}")
    files: list[Path] = []
    for pattern in ("*.yaml", "*.yml", "*.json"):
        files.extend(root.glob(pattern))
    return sorted(files)


def json_dumps(data: Any) -> str:
    """以稳定格式输出 JSON，便于报告和日志阅读。"""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def compact_json(data: Any) -> str:
    """输出紧凑 JSON，用于 HTTP 请求体和 tool arguments。"""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
