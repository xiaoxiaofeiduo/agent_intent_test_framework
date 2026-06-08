"""common.py 单元测试。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from ..common import compact_json, iter_data_files, json_dumps, load_json_compatible_yaml


class LoadJsonCompatibleYamlTests(SimpleTestCase):
    """测试 YAML/JSON 加载。"""

    def test_load_json_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write('{"key": "value"}')
        try:
            result = load_json_compatible_yaml(f.name)
            self.assertEqual(result, {"key": "value"})
        finally:
            Path(f.name).unlink()

    def test_load_yaml_file(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("cases:\n  - id: case1\n    name: 测试\n")
        try:
            result = load_json_compatible_yaml(f.name)
            self.assertIn("cases", result)
            self.assertEqual(len(result["cases"]), 1)
        finally:
            Path(f.name).unlink()

    def test_load_invalid_json_raises(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid: json: ]")
        try:
            with self.assertRaises((ValueError, Exception)):
                load_json_compatible_yaml(f.name)
        finally:
            Path(f.name).unlink()

    def test_load_json_compatible_yaml_as_json(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write('{"cases": [{"id": "test1"}]}')
        try:
            result = load_json_compatible_yaml(f.name)
            self.assertEqual(result, {"cases": [{"id": "test1"}]})
        finally:
            Path(f.name).unlink()


class IterDataFilesTests(SimpleTestCase):
    """测试数据文件迭代。"""

    def test_returns_sorted_yaml_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "c.yaml").write_text("{}")
            (Path(tmp) / "a.json").write_text("{}")
            (Path(tmp) / "b.yml").write_text("{}")
            files = iter_data_files(tmp)
            names = [f.name for f in files]
            self.assertEqual(names, ["a.json", "b.yml", "c.yaml"])

    def test_nonexistent_directory_raises(self) -> None:
        with self.assertRaises(FileNotFoundError):
            iter_data_files("/nonexistent/path/xyz")

    def test_empty_directory_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            files = iter_data_files(tmp)
            self.assertEqual(files, [])


class JsonDumpsTests(SimpleTestCase):
    """测试 JSON 序列化工具。"""

    def test_pretty_print(self) -> None:
        data = {"id": "test", "values": [1, 2]}
        result = json_dumps(data)
        self.assertIn('"id"', result)
        self.assertIn('"test"', result)
        parsed = json.loads(result)
        self.assertEqual(parsed, data)

    def test_unicode_preserved(self) -> None:
        result = json_dumps({"name": "测试"})
        self.assertIn("测试", result)
        # ensure_ascii=False 时不应出现 Unicode 转义
        self.assertNotIn("\\u", result)


class CompactJsonTests(SimpleTestCase):
    """测试紧凑 JSON。"""

    def test_no_extra_whitespace(self) -> None:
        data = {"a": 1, "b": "hello"}
        result = compact_json(data)
        self.assertNotIn(" ", result)
        parsed = json.loads(result)
        self.assertEqual(parsed, data)
