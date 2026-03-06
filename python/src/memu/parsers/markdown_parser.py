from __future__ import annotations

import logging
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger(__name__)


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def get_text(self) -> str:
        return "\n".join(self.parts)


def parse_markdown(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    try:
        import markdown

        html = markdown.markdown(raw)
        parser = _HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text().strip()
        return text or raw
    except Exception:
        logger.debug("markdown parser unavailable or failed for %s", path, exc_info=True)
        return raw
