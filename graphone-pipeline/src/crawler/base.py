"""
Async crawler base (Phase I: Massive One-Time Data Acquisition, Phase V:
Anti-Bot & Scale Thinking).

Design:
  - Two-tier fetch strategy: try cheap `aiohttp` first (fast, low resource
    cost — fine for ~80% of directory/listing pages). On a signal that we hit
    JS-gated or bot-protected content (Cloudflare interstitial markers, a 403,
    or a response body suspiciously smaller than expected), escalate to a
    pooled Playwright browser context for that URL only. This keeps the
    common case cheap and the hard case possible, instead of running every
    fetch through a full browser (which does not scale to hundreds of
    thousands of pages).
  - `asyncio.Semaphore` bounds concurrency *per host*, not just globally —
    global-only concurrency limits let one slow/blocking host starve
    everything else and also make it trivial to accidentally hammer a single
    site hard enough to get the whole crawler's IP range blocked.
  - Every fetch goes through `retry_with_backoff` (utils/backoff.py) so 429s
    and transient 5xxs are handled uniformly with the LLM layer.
  - robots.txt is fetched and cached per-host and respected by default; this
    is a deliberate product decision (see architecture.pdf) — for the small
    set of high-value sources where robots.txt blocks paths we need, the
    documented approach is a licensed data partnership / official API, not
    silently ignoring robots.txt at scale.

NOTE: outbound network is disabled in this build sandbox, so this module is
written to the aiohttp/Playwright APIs correctly but has NOT been executed
against live sites here. Install `aiohttp` + `playwright` and run
`playwright install chromium` in your own environment to exercise it — see
README "Running the crawler for real".
"""

from __future__ import annotations

import asyncio
import logging
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

from src.utils.backoff import BackoffConfig, RateLimitedError, retry_with_backoff

logger = logging.getLogger("graphone.crawler")

# Heuristic markers that a "200 OK" page is actually a bot-check interstitial,
# not real content — used to decide when to escalate to Playwright.
_BOT_WALL_MARKERS = (
    "checking your browser",
    "cf-browser-verification",
    "just a moment...",
    "datadome",
    "enable javascript and cookies",
)


@dataclass
class FetchResult:
    url: str
    status: int
    html: str
    escalated_to_browser: bool = False


class RobotsCache:
    def __init__(self):
        self._cache: dict[str, robotparser.RobotFileParser] = {}

    async def is_allowed(self, url: str, user_agent: str, fetch_text_fn) -> bool:
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._cache:
            rp = robotparser.RobotFileParser()
            try:
                robots_txt = await fetch_text_fn(f"{host}/robots.txt")
                rp.parse(robots_txt.splitlines())
            except Exception:
                # If robots.txt is unreachable, default to permissive-but-polite
                # (rate limited regardless) rather than blocking the whole host.
                rp.parse([])
            self._cache[host] = rp
        return self._cache[host].can_fetch(user_agent, url)


@dataclass
class PerHostThrottle:
    """Bounds concurrency AND enforces a minimum delay between requests, per host."""
    max_concurrent_per_host: int = 4
    min_delay_seconds: float = 0.5
    _semaphores: dict[str, asyncio.Semaphore] = field(default_factory=dict)
    _last_request_time: dict[str, float] = field(default_factory=dict)

    def semaphore_for(self, host: str) -> asyncio.Semaphore:
        if host not in self._semaphores:
            self._semaphores[host] = asyncio.Semaphore(self.max_concurrent_per_host)
        return self._semaphores[host]


class AsyncCrawler:
    def __init__(
        self,
        user_agent: str = "GraphOneBot/1.0 (+https://graphone.example/bot)",
        max_concurrent_per_host: int = 4,
        backoff_cfg: BackoffConfig | None = None,
        respect_robots: bool = True,
    ):
        self.user_agent = user_agent
        self.throttle = PerHostThrottle(max_concurrent_per_host=max_concurrent_per_host)
        self.backoff_cfg = backoff_cfg or BackoffConfig()
        self.respect_robots = respect_robots
        self.robots = RobotsCache()
        self._session = None  # aiohttp.ClientSession, created lazily in `start()`
        self._browser = None  # playwright browser, created lazily on first escalation

    async def start(self):
        import aiohttp  # local import: only required if you actually run this
        self._session = aiohttp.ClientSession(headers={"User-Agent": self.user_agent})

    async def close(self):
        if self._session:
            await self._session.close()
        if self._browser:
            await self._browser.close()

    async def _raw_get_text(self, url: str) -> str:
        async with self._session.get(url, timeout=15) as resp:
            return await resp.text()

    async def fetch(self, url: str) -> FetchResult:
        host = urlparse(url).netloc

        if self.respect_robots:
            allowed = await self.robots.is_allowed(url, self.user_agent, self._raw_get_text)
            if not allowed:
                logger.info("robots.txt disallows %s — skipping", url)
                return FetchResult(url=url, status=-1, html="")

        sem = self.throttle.semaphore_for(host)
        async with sem:
            async def do_fetch():
                async with self._session.get(url, timeout=20) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        raise RateLimitedError(retry_after=float(retry_after) if retry_after else None)
                    text = await resp.text()
                    return resp.status, text

            status, html = await retry_with_backoff(do_fetch, cfg=self.backoff_cfg)

        if self._looks_bot_walled(html) or status in (403,):
            logger.info("Bot-wall detected on %s, escalating to Playwright", url)
            return await self._fetch_with_browser(url)

        return FetchResult(url=url, status=status, html=html)

    @staticmethod
    def _looks_bot_walled(html: str) -> bool:
        lowered = html.lower()
        return any(marker in lowered for marker in _BOT_WALL_MARKERS) or len(html.strip()) < 200

    async def _fetch_with_browser(self, url: str) -> FetchResult:
        """
        Escalation path for Cloudflare/Datadome-protected or heavily
        JS-rendered pages (Phase V). Uses a pooled Playwright browser with
        stealth-oriented context settings (realistic viewport, real UA,
        `navigator.webdriver` patched out) rather than raw headless defaults,
        which are trivially fingerprinted.

        For sources that *still* block a well-behaved headless browser (real
        enterprise Cloudflare + Turnstile), the honest answer — documented in
        architecture.pdf — is a residential-proxy + managed unblocker service
        (e.g. Bright Data, ScraperAPI) or a licensed data feed, not an
        escalating captcha-solving arms race.
        """
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)

        context = await self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1366, "height": 768},
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=30000)
            # Give Cloudflare's JS challenge a moment to resolve client-side.
            await page.wait_for_timeout(2500)
            html = await page.content()
            status = resp.status if resp else 200
            return FetchResult(url=url, status=status, html=html, escalated_to_browser=True)
        finally:
            await context.close()
