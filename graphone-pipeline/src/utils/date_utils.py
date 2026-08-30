"""
Date normalization for the "Freshness Challenge" (Phase II).

Three tiers of extraction, tried in order, because real sites are inconsistent:

  1. Structured metadata  (JSON-LD `datePublished`, <meta property="article:published_time">,
     <time datetime=...>) — highest confidence, always preferred when present.
  2. Visible relative text ("2 hours ago", "yesterday", "3d", "Just now") — parsed
     relative to a supplied `reference_time` (the moment of the crawl), NOT
     datetime.now(), so unit tests are deterministic and re-crawls are consistent.
  3. Heuristic fallback — if a source exposes neither, we compare a content hash
     against the last-seen hash for that URL (see `is_new_since_last_run`) instead
     of guessing a timestamp. We do NOT fabricate a plausible-looking date; an
     entity with no reliable date is flagged `date_confidence="heuristic"` and
     is excluded from the strict "24h fresh" tabs unless the heuristic confirms
     it's new since the last crawler run.

This module has zero third-party dependencies so it can be unit tested without
network access or `pip install`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

_RELATIVE_PATTERN = re.compile(
    r"(?P<num>\d+)\s*(?P<unit>second|sec|minute|min|hour|hr|day|d|week|wk|month|year)s?\s*ago",
    re.IGNORECASE,
)

_UNIT_TO_TIMEDELTA_KW = {
    "second": "seconds", "sec": "seconds",
    "minute": "minutes", "min": "minutes",
    "hour": "hours", "hr": "hours",
    "day": "days", "d": "days",
    "week": "weeks", "wk": "weeks",
    # month/year are approximate on purpose — see NOTE below
}

_WORD_ALIASES = {
    "just now": timedelta(seconds=30),
    "moments ago": timedelta(minutes=1),
    "today": timedelta(hours=1),   # conservative: treat bare "today" as ~1h old, not 0
    "yesterday": timedelta(days=1, hours=12),
}


@dataclass
class DateExtractionResult:
    iso_timestamp: Optional[str]
    confidence: str  # "structured" | "relative" | "heuristic" | "none"
    is_fresh_24h: Optional[bool]  # None when we genuinely cannot tell


def parse_structured_date(meta_value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 / RFC-3339-ish string from JSON-LD or <meta> tags."""
    if not meta_value:
        return None
    candidate = meta_value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def parse_relative_date(text: str, reference_time: datetime) -> Optional[datetime]:
    """Parse visible relative-time strings like '2 hours ago', 'yesterday', '3d'."""
    if not text:
        return None
    normalized = text.strip().lower()

    if normalized in _WORD_ALIASES:
        return reference_time - _WORD_ALIASES[normalized]

    match = _RELATIVE_PATTERN.search(normalized)
    if match:
        num = int(match.group("num"))
        unit = match.group("unit").lower()
        if unit in ("month",):
            return reference_time - timedelta(days=30 * num)  # approximation, flagged below
        if unit in ("year",):
            return reference_time - timedelta(days=365 * num)
        kw = _UNIT_TO_TIMEDELTA_KW.get(unit)
        if kw:
            return reference_time - timedelta(**{kw: num})

    # bare "3d" / "5h" shorthand some job boards use (no "ago")
    short = re.fullmatch(r"(\d+)\s*([smhdw])", normalized)
    if short:
        num, unit = int(short.group(1)), short.group(2)
        unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
        return reference_time - timedelta(**{unit_map[unit]: num})

    return None


def normalize_publication_date(
    *,
    structured_meta: Optional[str],
    visible_relative_text: Optional[str],
    reference_time: Optional[datetime] = None,
) -> DateExtractionResult:
    """
    Try structured metadata first, then visible relative text. Returns an
    honest confidence label rather than silently degrading — downstream
    freshness filters key off `confidence` and `is_fresh_24h`, never off a
    bare timestamp alone.
    """
    reference_time = reference_time or datetime.now(timezone.utc)

    dt = parse_structured_date(structured_meta)
    if dt is not None:
        age = reference_time - dt
        return DateExtractionResult(
            iso_timestamp=dt.isoformat(),
            confidence="structured",
            is_fresh_24h=age <= timedelta(hours=24) and age >= timedelta(0),
        )

    dt = parse_relative_date(visible_relative_text or "", reference_time)
    if dt is not None:
        age = reference_time - dt
        return DateExtractionResult(
            iso_timestamp=dt.isoformat(),
            confidence="relative",
            is_fresh_24h=age <= timedelta(hours=24),
        )

    return DateExtractionResult(iso_timestamp=None, confidence="none", is_fresh_24h=None)


def is_new_since_last_run(
    url: str,
    content_hash: str,
    seen_hashes: dict[str, str],
) -> bool:
    """
    Heuristic fallback (Phase II, "Intelligent Heuristics") for sources with
    no reliable date at all: if we've never seen this URL, or its content hash
    changed since the last crawl, treat it as new. `seen_hashes` is the
    persisted {url: hash} map from the previous run (Redis/DB-backed in
    production — see architecture doc, section 3).
    """
    previous = seen_hashes.get(url)
    return previous is None or previous != content_hash
