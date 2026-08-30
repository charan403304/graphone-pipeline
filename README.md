# GraphOne / FrontierAtlas — Intelligence Graph Ingestion Pipeline

A scalable, fault-tolerant ingestion pipeline for the AI/venture Intelligence Graph
take-home: startups, products, research papers (+ GitHub metrics), 24h-fresh news,
and 24h-fresh jobs, with multi-tier LLM extraction, deterministic entity resolution,
and anti-bot crawling.

## Status of this repo — read this first

This was built and unit-tested in an environment with **no outbound network
access**, so it could not be run live against arXiv, GitHub, job boards, news
sites, or Google Sheets here. What that means concretely:

| Layer | Status |
|---|---|
| Schemas, date normalization, chunking, entity resolution | **Written AND unit-tested here** — 23/23 tests pass with zero `pip install`s (`python3 run_tests.py`), see below. |
| arXiv/PapersWithCode parsing logic, GitHub star correlation | **Written and tested against synthetic + real-shaped data** (see `sample_output/`), not run against the live arXiv API in this sandbox. |
| Crawler (`aiohttp`/Playwright), LLM provider adapters, Sheets writer | **Written against each library's real, documented API**, not executed live — needs your API keys / `pip install` / network to run. |
| `sample_output/one_verified_real_record.json` | **One genuinely real record**, fetched live via web search during this session (DeepSeek-R1, github.com/deepseek-ai/DeepSeek-R1, verified star count). Included to prove the schema and extraction path produce real, source-traceable output — **not** a substitute for the 1,000-record minimum, which requires actually running this pipeline against live sources for the trial period. |

I want to be direct about this rather than hand you a spreadsheet of invented
rows: the brief is explicit that hallucinated data is immediate disqualification,
and the honest deliverable at this stage is a working, tested architecture you
can point at real infrastructure — not fabricated numbers. Everything below is
real code, ready to run once you add credentials.

## Quick start

```bash
pip install -r requirements.txt
playwright install chromium

export GEMINI_API_KEY=...
export GROQ_API_KEY=...
export DEEPSEEK_API_KEY=...
export GITHUB_TOKEN=...              # raises GitHub REST limit 60/hr -> 5,000/hr
export GOOGLE_SHEETS_ID=...
export GOOGLE_SHEETS_CREDENTIALS_PATH=credentials.json

# research papers (the one target wired end-to-end in main.py as a worked example)
python -m src.main --target research_papers --limit 1000
```

Wiring `--target startups`, `--target products`, `--target news`, and
`--target jobs` the same way `run_research_papers()` is wired in `src/main.py`
is the fastest path to the remaining tabs — the crawler, LLM orchestrator,
entity resolver, and news/jobs freshness pipeline (`src/crawler/news_jobs_scraper.py`)
are all already implemented; `main.py` just needs a directory source URL list
per vertical (YC/Product Hunt for startups/products; your 5 chosen news sites
and 5 job boards for Phase II).

## Running the test suite (no pytest install needed)

```bash
PYTHONPATH=. python3 run_tests.py
```

This runs 23 tests covering date normalization (relative dates, freshness
windows, structured-vs-heuristic fallback), entity resolution (exact/alias/
fuzzy/unresolved tiers, false-positive avoidance), and LLM chunking/merge
logic — all pure-logic, zero network, zero external dependencies. `pytest`
also works if you have it installed (`pytest tests/`); `run_tests.py` exists
purely so this repo is verifiable in a locked-down environment like the one
it was built in.

If you'd rather use `pytest -k` selection, per-test fixtures, or coverage
reports, swap in real `pytest` — the test files are plain functions and need
no changes.

## Repository layout

```
src/
  schemas.py                     Startup/Product/ResearchPaper/Job/News dataclasses,
                                  built on stdlib (no pydantic dependency — see
                                  docstring for why, and how to swap it back in)
  utils/
    date_utils.py                Phase II freshness: structured -> relative ->
                                  heuristic date extraction, 24h window check
    backoff.py                   Full-jitter exponential backoff for 429s
  llm/
    chunking.py                  413-prevention: boilerplate strip, paragraph-
                                  aware chunking with overlap, per-field merge
    orchestrator.py               Phase III: Gemini Flash -> Groq Llama3 ->
                                  DeepSeek fallback chain, 429/413 handling
  entity_resolution/
    seed_entities.py             Mock DB of 50 canonical AI startups + aliases
    resolver.py                  Phase IV: exact -> alias -> fuzzy -> unresolved,
                                  writes the Entity Mapping Log as it goes
  crawler/
    base.py                      Phase I/V: async crawler, per-host concurrency,
                                  robots.txt, Cloudflare/Datadome escalation to
                                  Playwright
    arxiv_scraper.py             arXiv Atom API + GitHub star correlation
    paperswithcode_scraper.py    PapersWithCode API + HTML detail-page fallback
    news_jobs_scraper.py         Phase II: shared freshness/dedup pipeline for
                                  news + job sources
  storage/
    sheets_writer.py             Batched writer for the 6-tab Google Sheet
  pipeline.py                    Bounded-concurrency orchestration (Phase I scale)
  main.py                        CLI entry point

tests/                           23 passing unit tests, stdlib-only
sample_output/                   One genuinely verified real record (see above)
architecture.pdf                 Phase VI deliverable (2 pages)
build_architecture_pdf.py        Regenerates architecture.pdf from source
requirements.txt
```

## Design decisions worth knowing about before you extend this

- **`schemas.py` uses stdlib `dataclasses`, not pydantic.** One fewer
  dependency to build/pin across a worker fleet, and it's why the full test
  suite runs with zero `pip install`. Swapping to pydantic `BaseModel` is
  mechanical if your team prefers it elsewhere — constructors are already
  keyword-friendly for that migration.
- **Entity resolution is precision-first.** Below the fuzzy-match threshold,
  names are returned `unresolved` with a best-guess + score rather than
  auto-merged — a wrong merge (two companies collapsed into one) is worse
  than a name waiting for review, and the eval brief weights precision
  explicitly.
- **`is_fresh_24h` can be `None`.** A record with no reliable date signal is
  never assigned a guessed timestamp — it's either confirmed new via the
  content-hash heuristic (Phase II "Intelligent Heuristics") or dropped.
- **arXiv/PapersWithCode use their official bulk APIs, not HTML scraping.**
  This is the actual path to "lakhs of records" for the papers vertical —
  see `architecture.pdf` section 1.

## One thing worth flagging from the brief itself

The example task URL in the brief is `https://paperswithcode.co/paper/98456`
— note `.co`, not `.com`. I didn't crawl it as-is: PapersWithCode's real
domain is `paperswithcode.com`, and blindly hitting an unverified `.co`
domain is exactly the kind of unvalidated-source behavior that produces
non-"legitimate, valid source URL" data. Worth double-checking with the team
whether that's a typo in the brief or intentional.

## Honest next steps to actually hit the deliverable

1. Add the API keys above; run `python -m src.main --target research_papers --limit 1000` against the real arXiv API — this path is fully wired.
2. Wire `--target startups` / `--target products` against YC's public company API and/or Product Hunt, reusing `AsyncCrawler` + `LLMOrchestrator.extract()` exactly as `arxiv_scraper.py` does.
3. Pick your 5 news sources + 5 job boards, write a `SourceConfig` per source (a CSS-selector link extractor + field extractor — see the `SourceConfig` dataclass in `news_jobs_scraper.py`), pass them to `ingest_news_source` / `ingest_job_source`.
4. Point `SheetsWriter` at a real spreadsheet ID + service account credentials, call `write_all(...)` once each vertical has real data.
5. Push to GitHub, drop `architecture.pdf` in the repo root (already built), fill in the submission form.
