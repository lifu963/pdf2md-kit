"""
锁定 `frontend/index.html` 运行时配置面板里的模型下拉选项。

用户需要在设置栏直接选择支持的模型，而不是手动编辑 `config.yaml`。
"""

from __future__ import annotations

from pathlib import Path
import re
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[2]
INDEX_HTML = ROOT / "frontend" / "index.html"


class ConfigPanelModelOptionsTests(TestCase):
    def test_supported_models_appear_in_config_dropdown(self) -> None:
        source = INDEX_HTML.read_text(encoding="utf-8")
        select_match = re.search(
            r'<select id="cfg-model-name"[^>]*>(?P<body>.*?)</select>',
            source,
            re.DOTALL,
        )

        self.assertIsNotNone(select_match, "配置面板必须保留 #cfg-model-name 下拉框")
        options = re.findall(
            r'<option value="([^"]+)">',
            select_match.group("body") if select_match else "",
        )

        self.assertIn("doubao-seed-2-0-lite-260215", options)
        self.assertIn("doubao-seed-2-0-pro-260215", options)
        self.assertEqual(len(options), len(set(options)), "模型下拉选项不应出现重复值")
