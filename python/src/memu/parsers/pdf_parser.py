from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        parts = [(page.extract_text() or "").strip() for page in reader.pages]
        return "\n".join(part for part in parts if part)
    except Exception as pypdf_err:
        logger.debug("pypdf parse failed for %s", path, exc_info=True)
        try:
            import pdfplumber

            with pdfplumber.open(str(path)) as pdf:
                parts = [(page.extract_text() or "").strip() for page in pdf.pages]
            return "\n".join(part for part in parts if part)
        except Exception as plumber_err:
            msg = f"Failed to parse PDF {path}: pypdf={pypdf_err}; pdfplumber={plumber_err}"
            raise RuntimeError(msg) from plumber_err
