#!/usr/bin/env python3
"""Regression tests for XML normalization and recovery parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


PYTHON_DIR = Path(__file__).resolve().parent
if str(PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_DIR))
SRC_DIR = PYTHON_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from memu.app.memorize import MemorizeMixin


class _DummyMemorize(MemorizeMixin):
    pass


class XmlResponseNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = _DummyMemorize()

    def test_accepts_markdown_fenced_xml_with_preamble(self) -> None:
        raw = """
Here is the XML:
```xml
<skills>
  <memory>
    <content>Use ampersand &amp; escape correctly</content>
    <categories>
      <category>xml</category>
    </categories>
  </memory>
</skills>
```
"""
        parsed = self.parser._parse_memory_type_response_xml(raw)
        self.assertEqual(
            parsed,
            [{"content": "Use ampersand & escape correctly", "categories": ["xml"]}],
        )

    def test_accepts_xml_declaration_and_xml_prefix(self) -> None:
        raw = """XML:
<?xml version="1.0" encoding="utf-8"?>
<knowledge>
  <memory>
    <content>Remember the deployment URL</content>
    <categories>
      <category>infra</category>
    </categories>
  </memory>
</knowledge>
"""
        parsed = self.parser._parse_memory_type_response_xml(raw)
        self.assertEqual(
            parsed,
            [{"content": "Remember the deployment URL", "categories": ["infra"]}],
        )

    def test_recovers_memory_blocks_from_malformed_root(self) -> None:
        raw = """
<skills>
  <memory>
    <content>First broken & item</content>
    <categories>
      <category>debugging</category>
    </categories>
  </memory>
  <memory>
    <content>Second item</content>
    <categories>
      <category>ops</category>
    </categories>
  </memory>
"""
        parsed = self.parser._parse_memory_type_response_xml(raw)
        self.assertEqual(
            parsed,
            [
                {"content": "First broken & item", "categories": ["debugging"]},
                {"content": "Second item", "categories": ["ops"]},
            ],
        )


if __name__ == "__main__":
    unittest.main()
