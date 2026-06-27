"""
Sorachio-STS LLM Client
Async HTTP client for llama-server's OpenAI-compatible API.

Supports:
  - Chat completions (streaming and non-streaming)
  - Health checks
  - Automatic retry on transient errors
  - Server-Sent Events (SSE) streaming
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from utils.logging_setup import get_logger

log = get_logger("llm.client")


# ---------------------------------------------------------------------------
# Message / Response types
# ---------------------------------------------------------------------------

class Message:
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


# ---------------------------------------------------------------------------
# LlamaClient
# ---------------------------------------------------------------------------

class LlamaClient:
    """
    Async client for llama-server's OpenAI-compatible REST API.

    Features:
      - Streaming token generation via SSE
      - Non-streaming full completion
      - Health check endpoint
      - Configurable timeouts and retries
    """

    def __init__(
        self,
        base_url: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        top_p: float = 0.95,
        repeat_penalty: float = 1.1,
        timeout_s: float = 30.0,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self.repeat_penalty = repeat_penalty
        self.timeout_s = timeout_s
        self.max_retries = max_retries

        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(
                    connect=5.0,
                    read=self.timeout_s,
                    write=10.0,
                    pool=5.0,
                ),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self) -> bool:
        """Return True if the server is healthy and ready."""
        try:
            client = await self._get_client()
            resp = await client.get("/health", timeout=5.0)
            return resp.status_code == 200
        except Exception as e:
            log.debug(f"Health check failed: {e}")
            return False

    async def wait_for_ready(self, timeout_s: float = 60.0) -> bool:
        """Poll until server is ready or timeout expires."""
        deadline = asyncio.get_event_loop().time() + timeout_s
        attempt = 0
        while asyncio.get_event_loop().time() < deadline:
            if await self.health_check():
                log.info(f"Server ready at {self.base_url}")
                return True
            attempt += 1
            wait = min(2.0 * attempt, 10.0)
            log.debug(f"Server not ready, retrying in {wait:.1f}s...")
            await asyncio.sleep(wait)
        log.error(f"Server at {self.base_url} did not become ready in {timeout_s}s")
        return False

    async def complete(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> str:
        """
        Non-streaming chat completion.
        Returns the full assistant response as a string.
        """
        payload = self._build_payload(
            messages, temperature, max_tokens, stream=False, extra_params=extra_params
        )

        for attempt in range(self.max_retries):
            try:
                client = await self._get_client()
                resp = await client.post("/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                log.debug(f"Complete response ({len(content)} chars)")
                return content
            except httpx.HTTPStatusError as e:
                log.error(f"HTTP {e.response.status_code} from LLM server: {e}")
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(1.0 * (attempt + 1))
            except httpx.RequestError as e:
                detail = str(e) or "(no message — likely a connect/read timeout, often during model warm-up)"
                log.error(f"Request error (attempt {attempt + 1}): {type(e).__name__}: {detail}")
                if attempt == self.max_retries - 1:
                    raise
                await asyncio.sleep(1.0 * (attempt + 1))

        raise RuntimeError("All retries exhausted")

    async def stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra_params: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """
        Streaming chat completion via Server-Sent Events.
        Yields individual token deltas as strings.
        """
        payload = self._build_payload(
            messages, temperature, max_tokens, stream=True, extra_params=extra_params
        )

        client = await self._get_client()

        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
            timeout=httpx.Timeout(connect=5.0, read=self.timeout_s, write=10.0, pool=5.0),
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                if line.startswith("data: "):
                    line = line[6:]
                if line == "[DONE]":
                    break
                try:
                    data = json.loads(line)
                    delta = data["choices"][0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError) as e:
                    log.debug(f"Stream parse skip: {line!r} — {e}")
                    continue

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "top_p": self.top_p,
            "repeat_penalty": self.repeat_penalty,
            "stream": stream,
        }
        if extra_params:
            payload.update(extra_params)
        return payload
