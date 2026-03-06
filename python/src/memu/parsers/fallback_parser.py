from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_with_textract(path: Path) -> str:
    logger.warning("Unsupported extension for %s, using textract fallback", path)
    try:
        import textract

        extracted = textract.process(str(path))
        return extracted.decode("utf-8", errors="ignore")
    except Exception as exc:
        msg = f"Fallback textract failed for {path}: {exc}"
        raise RuntimeError(msg) from exc
