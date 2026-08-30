"""
Entry point. Reads provider credentials from environment variables and runs
the pipeline end to end.

    GEMINI_API_KEY=...
    GROQ_API_KEY=...
    DEEPSEEK_API_KEY=...
    GITHUB_TOKEN=...           # raises GitHub REST rate limit from 60/hr to 5,000/hr
    GOOGLE_SHEETS_ID=...
    GOOGLE_SHEETS_CREDENTIALS_PATH=credentials.json

Run with:  python -m src.main --target research_papers --limit 1000

This file is deliberately thin glue — see README.md for the full runbook,
including how to scale `--limit` toward the 500k target by pointing workers
at a shared queue (Redis/SQS) instead of the in-process asyncio.Queue used
for a single-machine run.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

from src.crawler.arxiv_scraper import ArxivQueryParams, build_paper_entity, build_query_url, parse_arxiv_atom_feed
from src.crawler.base import AsyncCrawler
from src.entity_resolution.resolver import EntityResolver
from src.llm.orchestrator import DeepSeekAdapter, GeminiFlashAdapter, GroqLlama3Adapter, LLMOrchestrator
from src.pipeline import Pipeline, PipelineConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("graphone.main")


def build_llm_orchestrator() -> LLMOrchestrator:
    async def http_post(url, payload, headers=None):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers or {}) as resp:
                body = await resp.json()
                return resp.status, body

    providers = [
        GeminiFlashAdapter(os.environ["GEMINI_API_KEY"], http_post),
        GroqLlama3Adapter(os.environ["GROQ_API_KEY"], http_post),
        DeepSeekAdapter(os.environ["DEEPSEEK_API_KEY"], http_post),
    ]
    return LLMOrchestrator(providers=providers)


async def run_research_papers(limit: int) -> None:
    async def http_get_json(url: str):
        import aiohttp
        headers = {}
        if "github.com" in url and os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                return await resp.json()

    async def http_get_text(url: str):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                return await resp.text()

    collected = []
    start = 0
    page_size = 100
    while len(collected) < limit:
        params = ArxivQueryParams(search_query="cat:cs.AI", start=start, max_results=page_size)
        xml_text = await http_get_text(build_query_url(params))
        raw_entries = parse_arxiv_atom_feed(xml_text)
        if not raw_entries:
            break
        for raw in raw_entries:
            entity = await build_paper_entity(raw, http_get_json)
            collected.append(entity)
            if len(collected) >= limit:
                break
        start += page_size
        logger.info("Collected %d/%d research papers so far", len(collected), limit)

    logger.info("Done. %d research papers collected.", len(collected))
    # -> hand off to storage/sheets_writer.SheetsWriter.write_all(...)


def main():
    parser = argparse.ArgumentParser(description="GraphOne / FrontierAtlas ingestion pipeline")
    parser.add_argument("--target", choices=["research_papers", "startups", "products", "news", "jobs"], required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()

    if args.target == "research_papers":
        asyncio.run(run_research_papers(args.limit))
    else:
        raise NotImplementedError(
            f"--target {args.target}: wire this the same way as run_research_papers() above, "
            f"using crawler/news_jobs_scraper.py or the startups/products scrapers you extend "
            f"from crawler/base.py's AsyncCrawler."
        )


if __name__ == "__main__":
    main()
