"""
Papers with Code integration (Phase I example task).

PapersWithCode's own public API (https://paperswithcode.com/api/v1/) exposes
paper<->repository links directly and is dramatically cheaper than scraping
rendered paper detail pages — this is the preferred path for volume. This
module also includes an HTML-detail-page parser as a fallback for records the
API doesn't cover (e.g. very recent papers not yet indexed), matching the
brief's example task shape (`/paper/<id>` detail pages).
"""

from __future__ import annotations

import re
from typing import Optional

from src.crawler.arxiv_scraper import GITHUB_URL_PATTERN

PWC_API_BASE = "https://paperswithcode.com/api/v1"


async def fetch_paper_repo_links(paper_id: str, http_get_json) -> list[dict]:
    """
    GET /api/v1/papers/{id}/repositories/ — returns repos implementing the
    paper, each with a `url`, `stars`, `is_official` flag. This single call
    replaces what would otherwise require rendering and scraping the HTML
    detail page's "Code" tab.
    """
    return await http_get_json(f"{PWC_API_BASE}/papers/{paper_id}/repositories/")


def parse_detail_page_html(html: str) -> dict:
    """
    Fallback HTML parse for a `/paper/<id>` detail page when the API hasn't
    indexed the record yet. Deliberately narrow (title + first GitHub link +
    any visible star count span) — this is a fallback path, not the primary
    extraction strategy, so it stays simple and is the first thing to hand to
    the LLM orchestrator (src/llm/orchestrator.py) rather than growing more
    regexes: unstructured HTML is exactly the case the LLM tier exists for.
    """
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else None

    github_match = GITHUB_URL_PATTERN.search(html)
    github_url = None
    if github_match:
        owner, repo = github_match.groups()
        github_url = f"https://github.com/{owner}/{repo.rstrip(').,')}"

    stars_match = re.search(r'([\d,]+)\s*stars?', html, re.IGNORECASE)
    stars: Optional[int] = None
    if stars_match:
        stars = int(stars_match.group(1).replace(",", ""))

    return {"title": title, "github_url": github_url, "github_stars_hint": stars}
