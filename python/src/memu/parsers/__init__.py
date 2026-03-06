from pathlib import Path

from memu.parsers.fallback_parser import parse_with_textract
from memu.parsers.markdown_parser import parse_markdown
from memu.parsers.pdf_parser import parse_pdf
from memu.parsers.text_parser import parse_text


def parse_file(file_path: str) -> str:
    path = Path(file_path).expanduser()
    if not path.exists():
        msg = f"File not found: {path}"
        raise FileNotFoundError(msg)

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in {".md", ".markdown", ".mkd", ".mdown"}:
        return parse_markdown(path)
    if suffix in {".txt", ".log", ".rst", ".json", ".yaml", ".yml", ".csv", ".tsv", ".py"}:
        return parse_text(path)
    return parse_with_textract(path)


def parse_file_to_text(file_path: str) -> str:
    return parse_file(file_path)

__all__ = ["parse_file", "parse_file_to_text"]
