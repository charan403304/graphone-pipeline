"""
Canonical output schemas for the GraphOne / FrontierAtlas ingestion pipeline.

These map 1:1 onto the "Expected Schemas" section of the assignment brief.
Every entity carries a `source.url` — the anti-hallucination invariant of this
whole system is: **no record is ever constructed without a concrete URL it was
extracted from.** LLM extraction functions receive raw text + source URL and
are only allowed to fill fields that text supports; they never invent one.

IMPLEMENTATION NOTE: built on stdlib `dataclasses` rather than pydantic.
This is a deliberate dependency-minimization choice for a fast-moving
ingestion pipeline running at high concurrency — one fewer dependency to pin
and build across worker fleets, and it means this file (and everything that
imports it) runs anywhere Python 3.10+ runs, no `pip install` required.
Swapping to pydantic `BaseModel` is a mechanical, low-risk change if the team
standardizes on it elsewhere — validation here is done explicitly in
`__post_init__` instead of via pydantic validators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

SCHEMA_VERSION = "1.0"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordType(str, Enum):
    STARTUP = "STARTUP"
    PRODUCT = "PRODUCT"
    RESEARCH_PAPER = "RESEARCH_PAPER"
    JOB = "JOB"
    NEWS = "NEWS"


class PricingModel(str, Enum):
    FREE = "FREE"
    FREEMIUM = "FREEMIUM"
    PAID = "PAID"
    ENTERPRISE = "ENTERPRISE"
    UNKNOWN = "UNKNOWN"  # honest fallback rather than a guessed enum value


@dataclass
class SourceRef:
    name: str
    url: str

    def __post_init__(self):
        if not (self.url.startswith("http://") or self.url.startswith("https://")):
            raise ValueError(f"source.url must be a real http(s) URL, got: {self.url!r}")


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

@dataclass
class StartupContentData:
    employeeCount: Optional[int] = None


@dataclass
class StartupContent:
    entityName: str  # CANONICAL name, post entity-resolution
    rawName: Optional[str] = None  # what was actually printed on the page, pre-resolution
    data: StartupContentData = field(default_factory=StartupContentData)


@dataclass
class StartupEntity:
    source: SourceRef
    content: StartupContent
    schemaVersion: str = SCHEMA_VERSION
    recordType: RecordType = RecordType.STARTUP
    collectedAt: str = field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

@dataclass
class ProductContent:
    startupName: str  # canonical parent startup name
    productName: Optional[str] = None
    pricingModel: PricingModel = PricingModel.UNKNOWN


@dataclass
class ProductEntity:
    source: SourceRef
    content: ProductContent
    schemaVersion: str = SCHEMA_VERSION
    recordType: RecordType = RecordType.PRODUCT
    collectedAt: str = field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Research Paper
# ---------------------------------------------------------------------------

@dataclass
class ResearchPaperContent:
    title: str
    paper_url: str
    authors: list[str] = field(default_factory=list)
    github_url: Optional[str] = None
    github_stars: Optional[int] = None
    published_date: Optional[str] = None  # ISO-8601


@dataclass
class ResearchPaperEntity:
    source: SourceRef
    content: ResearchPaperContent
    schemaVersion: str = SCHEMA_VERSION
    recordType: RecordType = RecordType.RESEARCH_PAPER
    collectedAt: str = field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------

@dataclass
class JobContent:
    company: str  # canonical
    date: str  # ISO-8601 publication date
    rawCompany: Optional[str] = None
    title: Optional[str] = None
    is_remote: bool = False
    role_family: str = "Unclassified"


@dataclass
class JobEntity:
    source: SourceRef
    content: JobContent
    schemaVersion: str = SCHEMA_VERSION
    recordType: RecordType = RecordType.JOB
    collectedAt: str = field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# News (not in the explicit schema table, but required for the News tab —
# modelled consistently with the others)
# ---------------------------------------------------------------------------

@dataclass
class NewsContent:
    headline: str
    full_text: str
    published_date: str  # ISO-8601, must be <24h old at ingest time
    entities_mentioned: list[str] = field(default_factory=list)


@dataclass
class NewsEntity:
    source: SourceRef
    content: NewsContent
    schemaVersion: str = SCHEMA_VERSION
    recordType: RecordType = RecordType.NEWS
    collectedAt: str = field(default_factory=utc_now_iso)


# ---------------------------------------------------------------------------
# Entity mapping log row (for the "Entity Mapping Log" tab)
# ---------------------------------------------------------------------------

@dataclass
class EntityMappingLogRow:
    raw_name: str
    canonical_name: str
    confidence: float
    method: str  # "exact" | "alias" | "fuzzy" | "llm_arbitration" | "unresolved"
    source_url: str
