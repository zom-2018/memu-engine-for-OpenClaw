from __future__ import annotations

from dataclasses import dataclass

import tiktoken


@dataclass
class TextChunk:
    text: str
    start_token: int
    end_token: int
    index: int


_ENCODER = tiktoken.get_encoding("cl100k_base")


def tokenize(text: str) -> list[int]:
    if not text:
        return []
    return _ENCODER.encode(text)


def detokenize(tokens: list[int]) -> str:
    if not tokens:
        return ""
    return _ENCODER.decode(tokens)


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[TextChunk]:
    if chunk_size <= 0:
        msg = "chunk_size must be > 0"
        raise ValueError(msg)
    if overlap < 0 or overlap >= chunk_size:
        msg = "overlap must be >= 0 and < chunk_size"
        raise ValueError(msg)

    tokens = tokenize(text)
    if not tokens:
        return []

    step = chunk_size - overlap
    chunks: list[TextChunk] = []
    start = 0

    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(
            TextChunk(
                text=detokenize(chunk_tokens),
                start_token=start,
                end_token=end,
                index=len(chunks),
            )
        )
        start += step

    return chunks
