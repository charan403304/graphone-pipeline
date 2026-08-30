"""
Intelligent chunking (Phase III) — ensures payloads never trigger 413s while
retaining semantically dense content.

Strategy:
  1. Cheap token estimate (chars/4 — good enough to stay well under provider
     limits without needing a tokenizer per-provider).
  2. If text fits under `max_tokens`, return it untouched.
  3. Otherwise, prefer splitting on structural boundaries (double newline /
     HTML block boundaries) over a blind character slice, so we don't cut a
     sentence — or a paper abstract — in half.
  4. A cheap "density" pre-filter strips boilerplate (nav/footer/cookie-banner
     text, repeated whitespace) BEFORE chunking, since that's the single
     biggest source of wasted tokens on scraped HTML-derived text.
  5. Each chunk is extracted independently and results are merged; for
     structured-JSON extraction targets (our case — startups/products/papers)
     the merge strategy is "first non-null wins per field", since the target
     entity's defining facts (name, URL) are almost always in the first
     dense chunk, while later chunks mostly re-confirm or add secondary
     detail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BOILERPLATE_PATTERNS = [
    re.compile(r"(?im)^\s*(cookie|subscribe|sign up|advertisement|newsletter).{0,80}$"),
    re.compile(r"\n{3,}"),
]

CHARS_PER_TOKEN_ESTIMATE = 4


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def strip_boilerplate(text: str) -> str:
    cleaned = text
    for pattern in _BOILERPLATE_PATTERNS[:-1]:
        cleaned = pattern.sub("", cleaned)
    cleaned = _BOILERPLATE_PATTERNS[-1].sub("\n\n", cleaned)
    return cleaned.strip()


@dataclass
class Chunk:
    index: int
    text: str
    estimated_tokens: int


def chunk_text(
    text: str,
    max_tokens: int = 6000,
    overlap_tokens: int = 150,
) -> list[Chunk]:
    """
    Split `text` into chunks that each stay under `max_tokens`, splitting on
    paragraph boundaries where possible. `overlap_tokens` of trailing context
    carries into the next chunk so facts that straddle a boundary (e.g. "the
    model, called X, achieves 94% accuracy" split across a paragraph break)
    aren't lost to either chunk.
    """
    text = strip_boilerplate(text)
    if estimate_tokens(text) <= max_tokens:
        return [Chunk(index=0, text=text, estimated_tokens=estimate_tokens(text))]

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0

    def flush():
        nonlocal current, current_tokens
        if current:
            joined = "\n\n".join(current)
            chunks.append(Chunk(index=len(chunks), text=joined, estimated_tokens=estimate_tokens(joined)))
        current, current_tokens = [], 0

    for para in paragraphs:
        para_tokens = estimate_tokens(para)

        # A single paragraph bigger than the whole budget (rare: a giant
        # unbroken block) gets hard-sliced rather than blowing the limit.
        if para_tokens > max_tokens:
            flush()
            slice_chars = max_tokens * CHARS_PER_TOKEN_ESTIMATE
            for i in range(0, len(para), slice_chars):
                piece = para[i:i + slice_chars]
                chunks.append(Chunk(index=len(chunks), text=piece, estimated_tokens=estimate_tokens(piece)))
            continue

        if current_tokens + para_tokens > max_tokens:
            flush()
            # carry overlap forward from the end of the previous chunk
            if chunks:
                prev_text = chunks[-1].text
                overlap_chars = overlap_tokens * CHARS_PER_TOKEN_ESTIMATE
                carry = prev_text[-overlap_chars:]
                current = [carry]
                current_tokens = estimate_tokens(carry)

        current.append(para)
        current_tokens += para_tokens

    flush()
    return chunks


def merge_extractions(field_dicts: list[dict]) -> dict:
    """
    'First non-null wins per field' merge across chunks extracted independently.
    Lists (e.g. `authors`) are unioned instead of overwritten.
    """
    merged: dict = {}
    for d in field_dicts:
        for k, v in d.items():
            if v is None or v == "":
                continue
            if k not in merged or merged[k] in (None, ""):
                merged[k] = v
            elif isinstance(merged[k], list) and isinstance(v, list):
                merged[k] = list(dict.fromkeys(merged[k] + v))  # union, order-preserving
    return merged
