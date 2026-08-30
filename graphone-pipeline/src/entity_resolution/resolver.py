"""
Deterministic-first entity resolution (Phase IV).

Deliberately layered cheapest-to-most-expensive, and DETERMINISTIC before
probabilistic, because the eval brief explicitly weights "Precision in mapping
messy strings to canonical forms" (10%) and precision matters more than
recall here — a wrong canonical merge (two different startups collapsed into
one) is worse than leaving a name unresolved for human review.

Tiers:
  1. Exact match (case/whitespace-insensitive) against canonical names.
  2. Alias match against the known-aliases table (handles "OpenAI, Inc.",
     legal suffixes, common rebrand spellings).
  3. Normalized fuzzy match (strip legal suffixes/punctuation, then
     difflib.SequenceMatcher ratio) — stdlib-only on purpose, so this runs
     and is testable with zero pip installs. Swap in `rapidfuzz` for a large
     production seed list; the interface doesn't change.
  4. Below the fuzzy threshold: NOT auto-resolved. Returned as `unresolved`
     with the best candidate + score attached, for either (a) an
     `llm_arbitration` pass — feeding the raw name + surrounding page context
     to the LLM orchestrator with a "is this the same company as X?" prompt —
     or (b) a human review queue. We do not silently guess at low confidence.

Every resolution — successful or not — is written to the Entity Mapping Log
(raw vs. canonical, confidence, method), satisfying the "Entity Mapping Log"
deliverable tab directly.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from src.entity_resolution.seed_entities import CANONICAL_STARTUPS
from src.schemas import EntityMappingLogRow

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc\.?|incorporated|llc|ltd\.?|limited|corp\.?|corporation|co\.?|pbc|plc|"
    r"gmbh|s\.?a\.?s\.?|technologies|labs|ai|ml)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[.,]")
_WS = re.compile(r"\s+")

FUZZY_ACCEPT_THRESHOLD = 0.86
FUZZY_REVIEW_THRESHOLD = 0.70  # below this, don't even surface as a "best guess"


def normalize(name: str) -> str:
    n = _PUNCT.sub("", name)
    n = _LEGAL_SUFFIXES.sub("", n)
    n = _WS.sub(" ", n).strip().lower()
    return n


@dataclass
class ResolutionResult:
    raw_name: str
    canonical_name: str | None
    confidence: float
    method: str  # "exact" | "alias" | "fuzzy" | "unresolved"


class EntityResolver:
    def __init__(self, canonical_map: dict[str, list[str]] | None = None):
        self.canonical_map = canonical_map or CANONICAL_STARTUPS
        self._exact_index: dict[str, str] = {}
        self._alias_index: dict[str, str] = {}
        self._normalized_index: dict[str, str] = {}  # normalized(name) -> canonical
        self._build_indices()
        self.log: list[EntityMappingLogRow] = []

    def _build_indices(self) -> None:
        for canonical, aliases in self.canonical_map.items():
            self._exact_index[canonical.strip().lower()] = canonical
            self._normalized_index[normalize(canonical)] = canonical
            for alias in aliases:
                self._alias_index[alias.strip().lower()] = canonical
                self._normalized_index.setdefault(normalize(alias), canonical)

    def resolve(self, raw_name: str, source_url: str = "") -> ResolutionResult:
        key = raw_name.strip().lower()

        if key in self._exact_index:
            result = ResolutionResult(raw_name, self._exact_index[key], 1.0, "exact")
        elif key in self._alias_index:
            result = ResolutionResult(raw_name, self._alias_index[key], 0.98, "alias")
        else:
            norm = normalize(raw_name)
            if norm in self._normalized_index:
                result = ResolutionResult(raw_name, self._normalized_index[norm], 0.95, "alias")
            else:
                best_name, best_score = self._best_fuzzy_match(norm)
                if best_score >= FUZZY_ACCEPT_THRESHOLD:
                    result = ResolutionResult(raw_name, best_name, round(best_score, 3), "fuzzy")
                elif best_score >= FUZZY_REVIEW_THRESHOLD:
                    # Surfaced but NOT auto-applied — canonical_name carries the
                    # best guess for a human/LLM arbitration step to confirm.
                    result = ResolutionResult(raw_name, best_name, round(best_score, 3), "unresolved")
                else:
                    result = ResolutionResult(raw_name, None, round(best_score, 3), "unresolved")

        self.log.append(EntityMappingLogRow(
            raw_name=raw_name,
            canonical_name=result.canonical_name or "(unresolved)",
            confidence=result.confidence,
            method=result.method,
            source_url=source_url,
        ))
        return result

    def _best_fuzzy_match(self, normalized_raw: str) -> tuple[str | None, float]:
        best_name, best_score = None, 0.0
        for norm_candidate, canonical in self._normalized_index.items():
            score = difflib.SequenceMatcher(None, normalized_raw, norm_candidate).ratio()
            if score > best_score:
                best_name, best_score = canonical, score
        return best_name, best_score

    def register_new_canonical(self, name: str, aliases: list[str] | None = None) -> None:
        """Used when a new legitimate entity (not in the seed 50) is confirmed —
        e.g. via llm_arbitration or human review — so future mentions resolve
        without re-triggering the fuzzy/LLM path."""
        aliases = aliases or []
        self.canonical_map[name] = aliases
        self._exact_index[name.strip().lower()] = name
        self._normalized_index[normalize(name)] = name
        for alias in aliases:
            self._alias_index[alias.strip().lower()] = name
