"""
Research Papers vertical (Phase I): arXiv listing -> paper detail -> associated
GitHub repo -> live star count.

arXiv specifically has a courteous, well-documented bulk API (the OAI-PMH
endpoint and the `export.arxiv.org/api/query` Atom feed) which is the correct
way to acquire "lakhs" of papers — NOT HTML scraping of arxiv.org/list/...,
which is both fragile and explicitly discouraged by arXiv's own crawling
policy. This is a concrete instance of the Phase V principle: prefer an
official bulk channel over adversarial scraping wherever one exists.

GitHub star counts come from the REST API (`GET /repos/{owner}/{repo}`),
which is dynamic and must be re-fetched at ingest time (not scraped once and
cached forever) per the "dynamic metrics" requirement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from xml.etree import ElementTree as ET

from src.schemas import ResearchPaperContent, ResearchPaperEntity, SourceRef

ARXIV_API_BASE = "http://export.arxiv.org/api/query"
GITHUB_URL_PATTERN = re.compile(r"https?://github\.com/([\w.\-]+)/([\w.\-]+)")

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


@dataclass
class ArxivQueryParams:
    search_query: str = "cat:cs.AI"  # e.g. "cat:cs.CL", "all:retrieval augmented generation"
    start: int = 0
    max_results: int = 100  # arXiv caps a single request; page with `start` for volume


def build_query_url(params: ArxivQueryParams) -> str:
    return (
        f"{ARXIV_API_BASE}?search_query={params.search_query}"
        f"&start={params.start}&max_results={params.max_results}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )


def parse_arxiv_atom_feed(xml_text: str) -> list[dict]:
    """Parse the Atom feed into lightweight dicts before LLM/GitHub enrichment."""
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        title = (entry.findtext("atom:title", default="", namespaces=_ATOM_NS) or "").strip()
        summary = (entry.findtext("atom:summary", default="", namespaces=_ATOM_NS) or "").strip()
        published = entry.findtext("atom:published", default=None, namespaces=_ATOM_NS)
        paper_url = entry.findtext("atom:id", default="", namespaces=_ATOM_NS)
        authors = [
            (a.findtext("atom:name", default="", namespaces=_ATOM_NS) or "").strip()
            for a in entry.findall("atom:author", _ATOM_NS)
        ]
        out.append({
            "title": title,
            "summary": summary,
            "published_date": published,
            "paper_url": paper_url,
            "authors": authors,
        })
    return out


def extract_github_url(text: str) -> Optional[str]:
    """arXiv abstracts frequently link the code repo directly in the text —
    cheapest possible extraction, tried before falling back to a
    Papers-with-Code cross-reference lookup."""
    match = GITHUB_URL_PATTERN.search(text)
    if match:
        owner, repo = match.groups()
        repo = repo.rstrip(").,")
        return f"https://github.com/{owner}/{repo}"
    return None


async def fetch_github_stars(owner: str, repo: str, http_get_json) -> Optional[int]:
    """
    `http_get_json` is an injected async callable (GET url -> parsed JSON),
    so this function is unit-testable with a fake without needing network.
    Real implementation: aiohttp GET to
    https://api.github.com/repos/{owner}/{repo}, with a GITHUB_TOKEN bearer
    header to get the authenticated 5,000/hr rate limit instead of 60/hr
    unauthenticated — essential at "lakhs of records" scale.
    """
    try:
        data = await http_get_json(f"https://api.github.com/repos/{owner}/{repo}")
        return data.get("stargazers_count")
    except Exception:
        return None


async def build_paper_entity(
    raw_entry: dict,
    http_get_json,
) -> ResearchPaperEntity:
    github_url = extract_github_url(raw_entry.get("summary", ""))
    stars = None
    if github_url:
        m = GITHUB_URL_PATTERN.search(github_url)
        if m:
            owner, repo = m.groups()
            stars = await fetch_github_stars(owner, repo, http_get_json)

    return ResearchPaperEntity(
        source=SourceRef(name="arXiv", url=raw_entry["paper_url"]),
        content=ResearchPaperContent(
            title=raw_entry["title"],
            authors=raw_entry.get("authors", []),
            paper_url=raw_entry["paper_url"],
            github_url=github_url,
            github_stars=stars,
            published_date=raw_entry.get("published_date"),
        ),
    )
