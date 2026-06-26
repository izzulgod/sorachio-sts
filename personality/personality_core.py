"""
Sorachio-STS Personality Core (LLM #2)
Streaming natural conversation engine using gemma-3-1b-it.

Responsibilities:
  - Generate natural, warm, emotionally-aware dialogue
  - Stream tokens to chunk assembler for real-time TTS
  - Maintain companion personality across conversation turns
  - Does NOT make routing or memory decisions (that's LLM #1's job)
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

from llm.llama_client import LlamaClient
from utils.chunk_assembler import ChunkAssembler
from utils.logging_setup import get_logger

log = get_logger("personality.core")


# ---------------------------------------------------------------------------
# PersonalityCore
# ---------------------------------------------------------------------------

class PersonalityCore:
    """
    LLM #2: Natural language generation engine.

    Streams tokens from gemma-3-1b-it, assembles them into speech chunks,
    and puts chunks into the TTS queue.
    """

    def __init__(
        self,
        client: LlamaClient,
        tts_queue: asyncio.Queue,
        interrupt_event: asyncio.Event,
        chunker_config: Optional[Dict[str, Any]] = None,
        temperature: float = 0.8,
        max_tokens: int = 512,
    ):
        self.client = client
        self.tts_queue = tts_queue
        self.interrupt_event = interrupt_event
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Chunk assembler config
        cfg = chunker_config or {}
        self._chunker = ChunkAssembler(
            min_words=cfg.get("min_words", 3),
            max_words=cfg.get("max_words", 30),
            sentence_endings=cfg.get("sentence_endings", [".", "!", "?", ";"]),
            flush_on_comma=cfg.get("flush_on_comma", False),
            flush_timeout_s=cfg.get("flush_timeout_s", 2.0),
        )

        self._current_task: Optional[asyncio.Task] = None
        self._full_response: str = ""

    async def generate_streaming(
        self,
        messages: List[Dict[str, str]],
    ) -> str:
        """
        Stream response from LLM #2, assemble chunks, queue for TTS.

        Returns the complete response text for storage in STM.
        """
        self.interrupt_event.clear()
        self._full_response = ""
        chunks_sent = 0

        log.info("[Personality] Starting streaming generation")

        try:
            token_stream = self.client.stream(
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )

            # Wrap token stream to track interruption
            async def interruptible_stream() -> AsyncIterator[str]:
                from core.events import get_bus, EventType
                bus = get_bus()
                async for token in token_stream:
                    if self.interrupt_event.is_set():
                        log.info("[Personality] Interrupted — stopping generation")
                        break
                    self._full_response += token
                    await bus.emit(EventType.RESPONSE_TOKEN, data=token, source="personality")
                    yield token

            # Process through chunk assembler
            async for chunk in self._chunker.process(interruptible_stream()):
                if self.interrupt_event.is_set():
                    break

                # Put chunk in TTS queue (non-blocking with timeout)
                try:
                    await asyncio.wait_for(
                        self.tts_queue.put(chunk),
                        timeout=5.0,
                    )
                    chunks_sent += 1
                    log.debug(f"[Personality] → TTS queue: {chunk!r}")
                except asyncio.TimeoutError:
                    log.warning("[Personality] TTS queue full — dropping chunk")

            log.info(
                f"[Personality] Complete: {len(self._full_response)} chars, "
                f"{chunks_sent} chunks sent"
            )

        except asyncio.CancelledError:
            log.info("[Personality] Task cancelled")
        except Exception as e:
            log.error(f"[Personality] Generation error: {e}", exc_info=True)

        return self._full_response

    def interrupt(self) -> None:
        """Signal the generation to stop."""
        self.interrupt_event.set()
        log.info("[Personality] Interrupt signal set")
