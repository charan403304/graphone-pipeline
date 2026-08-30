"""
Multi-tier LLM extraction engine (Phase III).

Fallback chain: Gemini Flash -> Groq (Llama 3) -> DeepSeek.
  - Gemini Flash first: cheapest per-token, fast, generous free-tier RPM —
    the right default for high-volume structured extraction.
  - Groq (Llama 3) second: near-instant inference, good safety net when
    Gemini rate-limits under burst load from many concurrent workers.
  - DeepSeek third: last resort — different vendor infra entirely, so a
    provider-wide outage on the first two doesn't stall the whole pipeline.

Each provider adapter raises `RateLimitedError` on 429 (handled by
utils.backoff) and `PayloadTooLargeError` on 413 (handled here by re-chunking
and retrying with a smaller chunk before falling back to the next tier).

NOTE ON CREDENTIALS: this module expects API keys via environment variables
(GEMINI_API_KEY, GROQ_API_KEY, DEEPSEEK_API_KEY). No keys are bundled — wire
them up via a `.env` file locally, or your secrets manager in production
(see README + architecture.pdf, section on ops).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from src.llm.chunking import chunk_text, merge_extractions
from src.utils.backoff import BackoffConfig, PayloadTooLargeError, RateLimitedError, retry_with_backoff

logger = logging.getLogger("graphone.llm")


EXTRACTION_SYSTEM_PROMPT = """You are a strict data-extraction engine. Given raw \
text scraped from a webpage and a target JSON schema, extract ONLY facts that \
are explicitly present in the text. If a field is not supported by the text, \
set it to null — never guess, infer, or fabricate a value. Respond with JSON \
only, no prose, no markdown fences."""


@dataclass
class ExtractionRequest:
    raw_text: str
    source_url: str
    target_schema_hint: str  # short description of the JSON shape we want back
    max_tokens_per_chunk: int = 6000


@dataclass
class ExtractionResult:
    data: dict[str, Any]
    provider_used: str
    chunks_used: int
    degraded: bool = False  # True if we fell back past the primary provider


class LLMProviderAdapter(ABC):
    name: str

    @abstractmethod
    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Call the provider, return parsed JSON. Raises RateLimitedError / PayloadTooLargeError."""


# ---------------------------------------------------------------------------
# Provider adapters — thin wrappers, one per vendor's HTTP API.
# These are written against each vendor's documented REST shape but are NOT
# exercised against live endpoints in this sandbox (no outbound network here).
# Swap `_http_post` for your actual async HTTP client (aiohttp) in your
# environment; the retry/chunking/merge logic above them is what's load-bearing
# and IS unit-tested (see tests/test_llm_chunking.py).
# ---------------------------------------------------------------------------

class GeminiFlashAdapter(LLMProviderAdapter):
    name = "gemini-flash"

    def __init__(self, api_key: str, http_post):
        self.api_key = api_key
        self._http_post = http_post  # injected async callable, see note above

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={self.api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"},
        }
        status, body = await self._http_post(url, payload)
        if status == 429:
            raise RateLimitedError(retry_after=_parse_retry_after(body))
        if status == 413:
            raise PayloadTooLargeError()
        text = body["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)


class GroqLlama3Adapter(LLMProviderAdapter):
    name = "groq-llama3"

    def __init__(self, api_key: str, http_post):
        self.api_key = api_key
        self._http_post = http_post

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        url = "https://api.groq.com/openai/v1/chat/completions"
        payload = {
            "model": "llama3-70b-8192",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        status, body = await self._http_post(
            url, payload, headers={"Authorization": f"Bearer {self.api_key}"}
        )
        if status == 429:
            raise RateLimitedError(retry_after=_parse_retry_after(body))
        if status == 413:
            raise PayloadTooLargeError()
        return json.loads(body["choices"][0]["message"]["content"])


class DeepSeekAdapter(LLMProviderAdapter):
    name = "deepseek"

    def __init__(self, api_key: str, http_post):
        self.api_key = api_key
        self._http_post = http_post

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        url = "https://api.deepseek.com/chat/completions"
        payload = {
            "model": "deepseek-chat",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        status, body = await self._http_post(
            url, payload, headers={"Authorization": f"Bearer {self.api_key}"}
        )
        if status == 429:
            raise RateLimitedError(retry_after=_parse_retry_after(body))
        if status == 413:
            raise PayloadTooLargeError()
        return json.loads(body["choices"][0]["message"]["content"])


def _parse_retry_after(body: Any) -> Optional[float]:
    if isinstance(body, dict):
        val = body.get("retry_after") or body.get("retryAfter")
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                return None
    return None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class LLMOrchestrator:
    providers: list[LLMProviderAdapter]  # in fallback order
    backoff_cfg: BackoffConfig = field(default_factory=BackoffConfig)

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        chunks = chunk_text(request.raw_text, max_tokens=request.max_tokens_per_chunk)
        user_prompt_template = (
            f"SOURCE URL: {request.source_url}\n"
            f"TARGET SCHEMA: {request.target_schema_hint}\n\n"
            "TEXT:\n{chunk_text}"
        )

        chunk_results: list[dict] = []
        provider_used = None
        degraded = False

        for chunk in chunks:
            result, used = await self._extract_one_chunk(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_prompt=user_prompt_template.format(chunk_text=chunk.text),
            )
            chunk_results.append(result)
            provider_used = provider_used or used
            if used != self.providers[0].name:
                degraded = True

        merged = merge_extractions(chunk_results)
        return ExtractionResult(
            data=merged,
            provider_used=provider_used or "none",
            chunks_used=len(chunks),
            degraded=degraded,
        )

    async def _extract_one_chunk(self, system_prompt: str, user_prompt: str) -> tuple[dict, str]:
        last_error: Exception | None = None

        for provider in self.providers:
            try:
                async def call():
                    return await provider.complete_json(system_prompt, user_prompt)

                result = await retry_with_backoff(
                    call,
                    cfg=self.backoff_cfg,
                    on_retry=lambda attempt, delay, e: logger.warning(
                        "429 from %s (attempt %d), backing off %.1fs", provider.name, attempt, delay
                    ),
                )
                return result, provider.name

            except PayloadTooLargeError:
                # This provider's limit is stricter than our chunker assumed.
                # Try a much smaller re-chunk of just this piece before giving
                # up on the provider entirely.
                logger.warning("413 from %s, re-chunking smaller and retrying same provider", provider.name)
                try:
                    sub_chunks = chunk_text(user_prompt, max_tokens=1500)
                    sub_results = []
                    for sc in sub_chunks:
                        r = await provider.complete_json(system_prompt, sc.text)
                        sub_results.append(r)
                    return merge_extractions(sub_results), provider.name
                except Exception as e:  # noqa: BLE001 — any failure here falls through to next provider
                    last_error = e
                    logger.warning("Re-chunked retry on %s also failed (%s); falling back", provider.name, e)
                    continue

            except RateLimitedError as e:
                last_error = e
                logger.warning("%s exhausted retries, falling back to next tier", provider.name)
                continue

            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.exception("%s raised an unexpected error, falling back", provider.name)
                continue

        raise RuntimeError(
            f"All LLM providers exhausted for this chunk. Last error: {last_error}"
        )
