"""
Phase II: High-Fidelity Signal Ingestion — news + jobs, both gated on the
strict "<24h fresh" requirement.

This module is source-agnostic on purpose: 5 news sources and 5 job boards
each have different DOM shapes, so per-source HTML parsing lives in a
`SOURCE_CONFIGS` table (selectors + a source-specific date-field extractor),
while the freshness/dedup/normalization logic below is shared. Adding a 6th
source means adding one config entry, not new pipeline code — directly
addresses the "without requiring code changes, only infrastructure scaling"
requirement from Phase I, applied here to Phase II sources too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from src.schemas import JobContent, JobEntity, NewsContent, NewsEntity, SourceRef
from src.utils.date_utils import normalize_publication_date


@dataclass
class SourceConfig:
    name: str
    base_url: str
    # Given a fetched listing page's HTML, return list of candidate detail URLs.
    extract_links: Callable[[str], list[str]]
    # Given a detail page's HTML, return (structured_date_meta, visible_relative_text, full_text).
    extract_fields: Callable[[str], tuple[Optional[str], Optional[str], str]]


@dataclass
class IngestStats:
    fetched: int = 0
    fresh_kept: int = 0
    stale_dropped: int = 0
    dedup_dropped: int = 0
    no_date_signal: int = 0


async def ingest_news_source(
    config: SourceConfig,
    crawler,  # AsyncCrawler instance
    seen_hashes: dict[str, str],
    reference_time: Optional[datetime] = None,
) -> tuple[list[NewsEntity], IngestStats]:
    """
    Full pipeline for one news source: fetch listing -> fetch each detail page
    -> normalize date -> enforce 24h freshness -> emit NewsEntity.

    A record is KEPT only if:
      (a) confidence is "structured" or "relative" AND is_fresh_24h is True, OR
      (b) confidence is "none" but the content-hash heuristic confirms it's
          new since the last crawler run (Phase II "Intelligent Heuristics").
    Anything else is dropped rather than guessed into the fresh bucket —
    false freshness is worse than a smaller-but-correct News tab.
    """
    reference_time = reference_time or datetime.now(timezone.utc)
    stats = IngestStats()
    entities: list[NewsEntity] = []

    listing = await crawler.fetch(config.base_url)
    stats.fetched += 1
    urls = config.extract_links(listing.html)

    for url in urls:
        page = await crawler.fetch(url)
        stats.fetched += 1
        structured_meta, relative_text, full_text = config.extract_fields(page.html)

        date_result = normalize_publication_date(
            structured_meta=structured_meta,
            visible_relative_text=relative_text,
            reference_time=reference_time,
        )

        content_hash = str(hash(full_text[:2000]))  # cheap stand-in; use SHA-256 in production
        heuristically_new = seen_hashes.get(url) != content_hash

        if date_result.confidence in ("structured", "relative"):
            if not date_result.is_fresh_24h:
                stats.stale_dropped += 1
                continue
        elif date_result.confidence == "none":
            stats.no_date_signal += 1
            if not heuristically_new:
                stats.dedup_dropped += 1
                continue
            # No reliable date AND we can't prove it's stale — kept, but the
            # timestamp is the crawl time with confidence flagged, never a
            # fabricated publication date.
            date_result.iso_timestamp = reference_time.isoformat()

        seen_hashes[url] = content_hash

        entities.append(NewsEntity(
            source=SourceRef(name=config.name, url=url),
            content=NewsContent(
                headline=full_text.split("\n", 1)[0][:200],
                full_text=full_text,
                published_date=date_result.iso_timestamp or reference_time.isoformat(),
            ),
        ))
        stats.fresh_kept += 1

    return entities, stats


async def ingest_job_source(
    config: SourceConfig,
    crawler,
    resolver,  # EntityResolver, to canonicalize the hiring company name
    role_classifier: Callable[[str], str],
    reference_time: Optional[datetime] = None,
) -> tuple[list[JobEntity], IngestStats]:
    """Same freshness discipline as news, plus company-name canonicalization
    and role-family classification (cheap keyword classifier by default;
    swap for an LLM call via the orchestrator for messier titles)."""
    reference_time = reference_time or datetime.now(timezone.utc)
    stats = IngestStats()
    entities: list[JobEntity] = []

    listing = await crawler.fetch(config.base_url)
    stats.fetched += 1
    urls = config.extract_links(listing.html)

    for url in urls:
        page = await crawler.fetch(url)
        stats.fetched += 1
        structured_meta, relative_text, full_text = config.extract_fields(page.html)

        date_result = normalize_publication_date(
            structured_meta=structured_meta,
            visible_relative_text=relative_text,
            reference_time=reference_time,
        )
        if date_result.confidence == "none" or not date_result.is_fresh_24h:
            stats.stale_dropped += 1
            continue

        raw_company = full_text.split("\n", 1)[0][:120]
        resolution = resolver.resolve(raw_company, source_url=url)

        entities.append(JobEntity(
            source=SourceRef(name=config.name, url=url),
            content=JobContent(
                company=resolution.canonical_name or raw_company,
                rawCompany=raw_company,
                date=date_result.iso_timestamp or reference_time.isoformat(),
                is_remote="remote" in full_text.lower(),
                role_family=role_classifier(full_text),
            ),
        ))
        stats.fresh_kept += 1

    return entities, stats


DEFAULT_ROLE_KEYWORDS: dict[str, list[str]] = {
    "Engineering": ["engineer", "developer", "swe", "infrastructure", "backend", "frontend"],
    "Research": ["research scientist", "research engineer", "phd"],
    "Data": ["data scientist", "data analyst", "analytics"],
    "Product": ["product manager", "product owner"],
    "Design": ["designer", "ux", "ui"],
    "Sales": ["sales", "account executive", "solutions engineer"],
}


def classify_role_family(text: str, keywords: dict[str, list[str]] = DEFAULT_ROLE_KEYWORDS) -> str:
    lowered = text.lower()
    for family, kws in keywords.items():
        if any(kw in lowered for kw in kws):
            return family
    return "Unclassified"
