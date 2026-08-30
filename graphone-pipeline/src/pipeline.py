"""
Top-level orchestration. This is intentionally the thinnest file in the repo —
it wires together modules that are each independently testable, rather than
burying control flow inside them.

Concurrency model at scale (Phase I "theoretically scale to 500,000+ records
without requiring code changes, only infrastructure scaling"):
  - `asyncio.Semaphore(GLOBAL_CONCURRENCY)` bounds total in-flight work; the
    crawler's own per-host semaphores (crawler/base.py) nest inside that.
  - Work is queued as (source, url) pairs pulled from an `asyncio.Queue`, with
    a configurable number of worker coroutines — going from 1,000 to 500,000
    records means raising `NUM_WORKERS` and `GLOBAL_CONCURRENCY` (and, past a
    single machine's ceiling, sharding the queue across worker processes/
    nodes via Redis or SQS — see architecture.pdf section 1) — no code path
    here changes shape as volume grows, only these numbers and where the
    queue lives.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from src.crawler.base import AsyncCrawler
from src.entity_resolution.resolver import EntityResolver
from src.llm.orchestrator import LLMOrchestrator
from src.schemas import JobEntity, NewsEntity, ProductEntity, ResearchPaperEntity, StartupEntity

logger = logging.getLogger("graphone.pipeline")


@dataclass
class PipelineConfig:
    num_workers: int = 50
    global_concurrency: int = 100


@dataclass
class PipelineResults:
    startups: list[StartupEntity] = field(default_factory=list)
    products: list[ProductEntity] = field(default_factory=list)
    papers: list[ResearchPaperEntity] = field(default_factory=list)
    jobs: list[JobEntity] = field(default_factory=list)
    news: list[NewsEntity] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        crawler: AsyncCrawler,
        llm: LLMOrchestrator,
        resolver: EntityResolver,
        config: PipelineConfig | None = None,
    ):
        self.crawler = crawler
        self.llm = llm
        self.resolver = resolver
        self.config = config or PipelineConfig()
        self.results = PipelineResults()
        self._global_sem = asyncio.Semaphore(self.config.global_concurrency)

    async def run_url_batch(self, urls: list[str], handler) -> None:
        """
        Generic bounded-concurrency map: fetch each URL and run `handler`
        (an async fn taking the fetched HTML + url) over it, capped at
        `global_concurrency` in-flight tasks regardless of batch size — the
        same code path handles a batch of 50 or 500,000 URLs.
        """
        queue: asyncio.Queue = asyncio.Queue()
        for url in urls:
            queue.put_nowait(url)

        async def worker():
            while not queue.empty():
                try:
                    url = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                async with self._global_sem:
                    try:
                        page = await self.crawler.fetch(url)
                        if page.status not in (-1,):  # -1 == robots.txt disallowed
                            await handler(page)
                    except Exception:
                        logger.exception("Failed processing %s", url)
                    finally:
                        queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(self.config.num_workers)]
        await asyncio.gather(*workers)
