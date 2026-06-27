"""
Sorachio-STS Chunk Assembler
Converts raw LLM token stream into natural speech chunks for TTS.

Chunks on:
  - Sentence boundaries (. ! ? ; ...)
  - Max word count exceeded
  - Timeout flush (configurable)

Does NOT chunk on:
  - Raw whitespace/newlines alone
  - Single tokens shorter than min_words
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator

from utils.logging_setup import get_logger

log = get_logger("chunker")


# ---------------------------------------------------------------------------
# Sentence boundary patterns
# ---------------------------------------------------------------------------
_SENTENCE_END = re.compile(
    r"""
    (?<=[.!?;])          # preceded by sentence-ending punctuation
    (?:\s+|$)            # followed by whitespace or end-of-string
    |
    \.{3}                # ellipsis (...)
    (?:\s+|$)
    """,
    re.VERBOSE,
)

_CLEANUP = re.compile(r"\s+")


def _word_count(text: str) -> int:
    return len(text.split())


def _clean(text: str) -> str:
    return _CLEANUP.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Chunk Assembler
# ---------------------------------------------------------------------------

class ChunkAssembler:
    """
    Assembles LLM token stream into TTS-ready speech chunks.

    Usage:
        assembler = ChunkAssembler(config)
        async for chunk in assembler.process(token_stream):
            await tts_queue.put(chunk)
    """

    def __init__(
        self,
        min_words: int = 3,
        max_words: int = 30,
        sentence_endings: list[str] | None = None,
        flush_on_comma: bool = False,
        flush_timeout_s: float = 2.0,
    ):
        self.min_words = min_words
        self.max_words = max_words
        self.sentence_endings = sentence_endings or [".", "!", "?", ";", "..."]
        self.flush_on_comma = flush_on_comma
        self.flush_timeout_s = flush_timeout_s

        self._buffer: str = ""
        self._last_token_time: float = 0.0

    def reset(self) -> None:
        """Reset internal buffer — call between conversations."""
        self._buffer = ""
        self._last_token_time = 0.0

    def _should_flush(self, text: str) -> bool:
        """Determine if current buffer should be flushed as a chunk."""
        stripped = text.rstrip()

        # Sentence-ending punctuation
        if stripped and stripped[-1] in {".", "!", "?", ";"}:
            if _word_count(text) >= self.min_words:
                return True

        # Ellipsis
        if stripped.endswith("...") and _word_count(text) >= self.min_words:
            return True

        # Comma flush (optional)
        if self.flush_on_comma and stripped.endswith(","):
            if _word_count(text) >= self.min_words:
                return True

        # Max words overflow
        if _word_count(text) >= self.max_words:
            return True

        return False

    async def process(
        self,
        token_stream: AsyncIterator[str],
    ) -> AsyncIterator[str]:
        """
        Consume async token stream, yield speech chunks.

        Args:
            token_stream: AsyncIterator that yields individual tokens/deltas

        Yields:
            str: complete speech chunks ready for TTS
        """
        self.reset()

        async for token in token_stream:
            self._buffer += token
            self._last_token_time = time.monotonic()

            # Check for sentence boundary in the accumulated buffer
            # We try splitting on sentence boundaries
            chunks = self._split_on_boundaries(self._buffer)

            if len(chunks) > 1:
                # Yield all complete chunks, keep last partial
                for chunk in chunks[:-1]:
                    chunk = _clean(chunk)
                    if chunk and _word_count(chunk) >= self.min_words:
                        log.debug(f"[Chunker] Emitting: {chunk!r}")
                        yield chunk
                    elif chunk:
                        # Too short — prepend to next chunk
                        chunks[-1] = chunk + " " + chunks[-1]

                self._buffer = chunks[-1]

        # Flush remaining buffer at stream end
        if self._buffer.strip():
            final = _clean(self._buffer)
            if final:
                log.debug(f"[Chunker] Final flush: {final!r}")
                yield final
            self._buffer = ""

    def _split_on_boundaries(self, text: str) -> list[str]:
        """
        Split text on sentence boundaries. Returns list of segments.
        The last segment is always the incomplete/current one.
        """
        # Split on . ! ? ; followed by whitespace
        pattern = r'(?<=[.!?;])\s+'
        parts = re.split(pattern, text)

        if len(parts) == 1:
            # Also check max_words overflow
            if _word_count(text) >= self.max_words:
                # Force split at last sentence boundary or midpoint
                words = text.split()
                mid = self.max_words
                return [" ".join(words[:mid]), " ".join(words[mid:])]
            return parts

        return parts


# ---------------------------------------------------------------------------
# Convenience wrapper for single-string splitting
# ---------------------------------------------------------------------------

def split_into_chunks(
    text: str,
    min_words: int = 3,
    max_words: int = 30,
) -> list[str]:
    """
    Split a complete text into TTS-ready chunks synchronously.
    Useful for testing or pre-processing.
    """
    ChunkAssembler(min_words=min_words, max_words=max_words)
    pattern = r'(?<=[.!?;])\s+'
    parts = re.split(pattern, text)
    chunks = []
    current = ""

    for part in parts:
        current = (current + " " + part).strip() if current else part
        if _word_count(current) >= min_words:
            chunks.append(_clean(current))
            current = ""

    if current.strip():
        if chunks:
            # Append to last chunk if too short
            chunks[-1] = _clean(chunks[-1] + " " + current)
        else:
            chunks.append(_clean(current))

    return chunks
