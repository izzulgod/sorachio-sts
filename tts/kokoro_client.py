"""
Sorachio-STS Kokoro TTS Client
Streaming text-to-speech synthesis using the kokoro Python library.

Pipeline:
  speech chunk (string) → Kokoro synthesis → numpy audio array → playback queue

Features:
  - In-process synthesis (no subprocess overhead)
  - Streams per-chunk audio immediately
  - Falls back gracefully if kokoro unavailable
  - Configurable voice, speed, and language
  - Defensive sanitization for unstable TTS input
"""

from __future__ import annotations

import asyncio

import numpy as np

from utils.logging_setup import get_logger

log = get_logger("tts.kokoro")


# ---------------------------------------------------------------------------
# KokoroTTSClient
# ---------------------------------------------------------------------------

class KokoroTTSClient:
    """
    Kokoro TTS wrapper that synthesizes text chunks and queues audio.

    Each text chunk is synthesized synchronously in an executor
    (to avoid blocking the event loop) and the audio is placed
    in the audio playback queue for immediate playback.
    """

    def __init__(
        self,
        audio_queue: asyncio.Queue,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "a",
        sample_rate: int = 24000,
    ):
        self.audio_queue = audio_queue
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.sample_rate = sample_rate

        self._pipeline = None
        self._available = False

    async def initialize(self) -> bool:
        """Load Kokoro model (blocking, run once at startup)."""
        loop = asyncio.get_event_loop()
        ok = await loop.run_in_executor(None, self._load_pipeline)

        self._available = ok

        if ok:
            log.info(
                f"[TTS] Kokoro ready — voice={self.voice} "
                f"speed={self.speed} lang={self.lang}"
            )
        else:
            log.warning(
                "[TTS] Kokoro not available — install with: pip install kokoro"
            )

        return ok

    def _load_pipeline(self) -> bool:
        """Load Kokoro pipeline in thread (avoids blocking event loop)."""

        try:
            from kokoro import KPipeline

            # ----------------------------------------------------------------
            # Kokoro language mapping
            #
            # a = American English
            # b = British English
            # ----------------------------------------------------------------

            lang_lower = self.lang.lower()

            if lang_lower in ["a", "en", "en-us", "us"]:
                lang_code = "a"
            elif lang_lower in ["b", "en-gb", "gb", "uk"]:
                lang_code = "b"
            else:
                log.warning(
                    f"[TTS] Unknown language '{self.lang}', defaulting to American English"
                )
                lang_code = "a"

            self._pipeline = KPipeline(lang_code=lang_code)

            # Warmup synthesis to preload model/voice
            try:
                generator = self._pipeline(
                    "Hello",
                    voice=self.voice,
                    speed=self.speed,
                    split_pattern=None,
                )

                for result in generator:
                    # Kokoro may return (gs, ps, audio) or (ps, audio) depending on version
                    # Always take the last element which is the audio array
                    _ = result[-1]
                    break

                log.info("[TTS] Kokoro warmup complete")

            except Exception as warmup_error:
                log.warning(f"[TTS] Warmup failed: {warmup_error}")

            return True

        except ImportError:
            log.error(
                "[TTS] kokoro not installed. Run: pip install kokoro[onnx]"
            )
            return False

        except Exception as e:
            log.error(f"[TTS] Failed to load Kokoro: {e}", exc_info=True)
            return False

    def _sanitize_text(self, text: str) -> str:
        """
        Clean problematic text before sending to Kokoro.
        Prevents crashes in misaki phoneme pipeline.
        """

        if not text:
            return ""

        text = text.strip()

        # Remove problematic control chars
        text = "".join(ch for ch in text if ord(ch) >= 32)

        # Replace problematic formatting chars
        replacements = {
            "*": "",
            "#": "",
            "`": "",
            "_": " ",
            "~": "",
            "|": "",
            "[": "",
            "]": "",
            "{": "",
            "}": "",
            "<": "",
            ">": "",
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        # Normalize whitespace
        text = " ".join(text.split())

        return text

    async def synthesize_chunk(self, text: str) -> np.ndarray | None:
        """
        Synthesize a single text chunk to audio.

        Returns numpy array of audio samples, or None on failure.
        Runs synthesis in thread executor to not block event loop.
        """

        text = self._sanitize_text(text)

        if not text:
            return None

        loop = asyncio.get_event_loop()

        def _synth():

            if not self._available or self._pipeline is None:
                return None

            try:
                log.debug(f"[TTS] Sanitized chunk: {text!r}")

                generator = self._pipeline(
                    text,
                    voice=self.voice,
                    speed=self.speed,
                    split_pattern=None,  # external chunking already handled
                )

                audio_segments = []

                try:
                    for result in generator:
                        # Kokoro may return (gs, ps, audio) or (ps, audio) depending on version
                        # Always take the last element which is the audio array
                        audio = result[-1]

                        if audio is None:
                            continue

                        if not hasattr(audio, '__len__') or len(audio) == 0:
                            continue

                        audio_segments.append(audio)

                except Exception as gen_error:
                    log.warning(
                        f"[TTS] Generator chunk failed: {gen_error}"
                    )

                    if isinstance(gen_error, TypeError) and "NoneType" in str(gen_error):
                        log.warning(
                            "[TTS] This usually means espeak-ng is missing or not on "
                            "PATH (misaki uses it as a fallback for words outside "
                            "Kokoro's built-in dictionary). Install it from "
                            "https://github.com/espeak-ng/espeak-ng/releases and "
                            "restart your terminal."
                        )

                    return None

                if audio_segments:
                    return np.concatenate(audio_segments)

                return None

            except Exception as e:
                log.error(f"[TTS] Synthesis error: {e}", exc_info=True)
                return None

        audio = await loop.run_in_executor(None, _synth)

        return audio

    async def process_tts_queue(
        self,
        tts_chunk_queue: asyncio.Queue,
        interrupt_event: asyncio.Event,
    ) -> None:
        """
        Worker: drain TTS chunk queue, synthesize each chunk, push to audio queue.

        This is the TTS worker loop. Call as an asyncio task.
        """

        while True:

            try:
                chunk = await asyncio.wait_for(
                    tts_chunk_queue.get(),
                    timeout=0.5,
                )

            except asyncio.TimeoutError:
                continue

            except asyncio.CancelledError:
                break

            if chunk is None:
                # End-of-stream sentinel
                await self.audio_queue.put(None)
                tts_chunk_queue.task_done()
                continue

            if interrupt_event.is_set():
                tts_chunk_queue.task_done()
                continue

            try:
                log.debug(f"[TTS] Synthesizing: {chunk!r}")

                audio = await self.synthesize_chunk(chunk)

                if audio is not None and not interrupt_event.is_set():

                    await self.audio_queue.put(audio)

                    log.debug(
                        f"[TTS] → Audio queue ({len(audio)} samples)"
                    )

            except Exception as worker_error:
                log.error(
                    f"[TTS] Worker error: {worker_error}",
                    exc_info=True,
                )

            finally:
                tts_chunk_queue.task_done()

    async def speak(self, text: str) -> None:
        """
        Convenience: synthesize full text and queue all audio directly.
        Used for startup greeting and test mode.
        """

        from utils.chunk_assembler import split_into_chunks

        chunks = split_into_chunks(
            text,
            min_words=2,
            max_words=25,
        )

        if not chunks:
            chunks = [text]

        for chunk in chunks:

            try:
                audio = await self.synthesize_chunk(chunk)

                if audio is not None:
                    await self.audio_queue.put(audio)

                    # tiny natural pause between chunks
                    await asyncio.sleep(0.05)

            except Exception as e:
                log.warning(f"[TTS] Speak chunk failed: {e}")

        # End-of-stream sentinel
        await self.audio_queue.put(None)
