"""
Exponential backoff with full jitter (AWS Architecture Blog algorithm), used for
both LLM provider 429s and crawler-side rate limiting.

We use "full jitter" rather than plain exponential backoff because at high
concurrency (thousands of coroutines), synchronized retries from a fixed
exponential schedule create thundering-herd spikes that trip the *next* rate
limit too. Full jitter decorrelates retries across the worker pool.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class RateLimitedError(Exception):
    """Raised by a provider adapter on HTTP 429."""
    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(f"Rate limited (retry_after={retry_after})")


class PayloadTooLargeError(Exception):
    """Raised by a provider adapter on HTTP 413. Caller must re-chunk, not retry as-is."""


@dataclass
class BackoffConfig:
    base_seconds: float = 1.0
    max_seconds: float = 60.0
    max_retries: int = 6


def compute_delay(attempt: int, cfg: BackoffConfig, retry_after: float | None = None) -> float:
    """
    Full-jitter exponential backoff: delay = random(0, min(max, base * 2^attempt)).
    If the provider gave us a Retry-After header, that's authoritative — we
    respect it (plus a small jitter) instead of guessing.
    """
    if retry_after is not None:
        return retry_after + random.uniform(0, 0.5)
    ceiling = min(cfg.max_seconds, cfg.base_seconds * (2 ** attempt))
    return random.uniform(0, ceiling)


async def retry_with_backoff(
    fn: Callable[[], Awaitable[T]],
    cfg: BackoffConfig | None = None,
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> T:
    """
    Runs `fn()`, retrying on RateLimitedError with exponential backoff + jitter.
    Any other exception propagates immediately — we never blindly retry on
    unknown errors (that would mask real bugs as "just rate limiting").
    """
    cfg = cfg or BackoffConfig()
    last_exc: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await fn()
        except RateLimitedError as e:
            last_exc = e
            if attempt == cfg.max_retries:
                break
            delay = compute_delay(attempt, cfg, retry_after=e.retry_after)
            if on_retry:
                on_retry(attempt, delay, e)
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc
