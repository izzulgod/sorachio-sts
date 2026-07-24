"""
Sorachio-STS Kokoro & Hybrid TTS Client
Streaming text-to-speech synthesis using Kokoro for English and Piper for Indonesian.

Pipeline:
  speech chunk (string) → Language Router → TTS Engine (Kokoro / Piper)
  → numpy audio array (resampled to target rate) → playback queue

Features:
  - Kokoro TTS for natural, high-quality English voice synthesis (af_heart)
  - Piper TTS for fast Indonesian voice synthesis (id_ID-news_tts-medium)
  - In-process streaming synthesis (no subprocess overhead)
  - Automatic language detection & STT language lock support
  - Auto-resampling to target sample rate (24000 Hz)
  - Defensive sanitization for text formatting and emojis
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import numpy as np

from tts.piper_client import PiperTTSClient
from utils.logging_setup import get_logger

log = get_logger("tts.kokoro")


def _resample_audio(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample 1D float32 audio array using linear interpolation."""
    if orig_sr == target_sr or len(audio) == 0:
        return audio
    num_samples = int(round(len(audio) * target_sr / orig_sr))
    indices = np.linspace(0, len(audio) - 1, num_samples)
    return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


# ---------------------------------------------------------------------------
# KokoroTTSClient
# ---------------------------------------------------------------------------

class KokoroTTSClient:
    """
    Hybrid TTS Client combining Kokoro (English) and Piper (Indonesian).

    Synthesizes text chunks asynchronously without blocking the event loop
    and places audio arrays into the audio queue for playback.
    """

    def __init__(
        self,
        audio_queue: asyncio.Queue,
        voice: str = "af_heart",
        speed: float = 1.0,
        lang: str = "auto",
        sample_rate: int = 24000,
        models_dir: str = "models/tts",
    ):
        self.audio_queue = audio_queue
        self.voice = voice
        self.speed = speed
        self.lang = lang
        self.sample_rate = sample_rate
        self.models_dir = Path(models_dir)

        # Kokoro pipeline (English)
        self._pipeline = None
        self._kokoro_available = False

        # Piper client (Indonesian fallback / primary for 'id')
        self._piper_client = PiperTTSClient(
            audio_queue=asyncio.Queue(),  # Internal queue for direct chunk synthesis
            voice="id_ID-news_tts-medium",
            speed=speed,
            lang="id",
            sample_rate=22050,
            models_dir=str(models_dir),
        )
        self._piper_available = False

        self._current_lang = "en"
        self._stt_lang_locked = False
        self._available = False

    async def initialize(self) -> bool:
        """Initialize both Kokoro (English) and Piper (Indonesian) models."""
        loop = asyncio.get_event_loop()

        # Load Kokoro in thread pool
        kokoro_ok = await loop.run_in_executor(None, self._load_kokoro)
        self._kokoro_available = kokoro_ok

        # Load Piper for Indonesian
        try:
            piper_ok = await self._piper_client.initialize()
            self._piper_available = piper_ok
        except Exception as e:
            log.warning(f"[TTS] Piper initialization failed: {e}")
            self._piper_available = False

        self._available = self._kokoro_available or self._piper_available

        if self._kokoro_available and self._piper_available:
            log.info(
                f"[TTS] Hybrid TTS ready — English: Kokoro ({self.voice}) | "
                f"Indonesian: Piper (id_ID-news_tts-medium)"
            )
        elif self._kokoro_available:
            log.info(f"[TTS] Kokoro TTS ready — voice={self.voice} (English active)")
        elif self._piper_available:
            log.info("[TTS] Piper TTS ready (Indonesian active)")
        else:
            log.warning("[TTS] No TTS engines available!")

        return self._available

    def _load_kokoro(self) -> bool:
        """Load Kokoro pipeline in thread."""
        try:
            kokoro_models_dir = self.models_dir / "kokoro"
            kokoro_models_dir.mkdir(parents=True, exist_ok=True)
            os.environ["HF_HOME"] = str(kokoro_models_dir)

            from kokoro import KPipeline

            lang_lower = self.lang.lower()
            if lang_lower in ["b", "en-gb", "gb", "uk"]:
                lang_code = "b"
            else:
                lang_code = "a"  # American English default

            self._pipeline = KPipeline(
                lang_code=lang_code,
                repo_id="hexgrad/Kokoro-82M",
            )

            # Warmup synthesis
            try:
                generator = self._pipeline(
                    "Hello",
                    voice=self.voice,
                    speed=self.speed,
                    split_pattern=None,
                )
                for result in generator:
                    _ = result[-1]
                    break
                log.info("[TTS] Kokoro warmup complete [OK]")
            except Exception as warmup_error:
                log.warning(f"[TTS] Kokoro warmup failed: {warmup_error}")

            return True

        except ImportError:
            log.error("[TTS] kokoro package not installed. Run: pip install kokoro")
            return False
        except Exception as e:
            log.error(f"[TTS] Failed to load Kokoro: {e}", exc_info=True)
            return False

    def set_language(self, lang: str, from_stt: bool = False) -> None:
        """
        Set the active language for TTS routing.
        Called when STT detects user language or language preference changes.
        """
        if from_stt:
            self._stt_lang_locked = True

        target = "id" if lang and lang.lower().startswith("id") else "en"
        if target != self._current_lang:
            log.info(f"[TTS] Language routing changed: {self._current_lang} → {target}")
            self._current_lang = target

        if self._piper_client:
            self._piper_client.set_language(lang, from_stt=from_stt)

    def _detect_text_language(self, text: str) -> str:
        """Detect if text is Indonesian ('id') or English ('en')."""
        if not text:
            return "en"

        id_keywords = {
            "saya", "aku", "kamu", "dengan", "senang", "halo", "nama", "terima", "kasih",
            "apa", "bisa", "ini", "itu", "yang", "dan", "untuk", "ada", "bicarakan",
            "perkenalkan", "diri", "hari", "merasa", "teman", "setia", "sekali", "baik",
            "ya", "sih", "kok", "aja", "udah", "kan", "dong", "bagus", "siapa", "dimana"
        }
        words = set(re.findall(r"\b\w+\b", text.lower()))
        if len(words.intersection(id_keywords)) >= 1:
            return "id"

        try:
            from langdetect import DetectorFactory, detect
            DetectorFactory.seed = 0
            detected = detect(text)
            if detected in ("id", "ms", "jw", "su"):
                return "id"
        except Exception:
            pass

        return "en"

    def _sanitize_text(self, text: str) -> str:
        """Clean problematic characters before TTS synthesis."""
        if not text:
            return ""

        text = text.strip()

        # Remove control chars
        text = "".join(ch for ch in text if ord(ch) >= 32)

        # Remove formatting symbols
        replacements = {
            "*": "", "#": "", "`": "", "_": " ", "~": "", "|": "",
            "[": "", "]": "", "{": "", "}": "", "<": "", ">": "",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)

        # Remove Unicode emojis (high-plane code points)
        text = re.sub(r'[\U00010000-\U0010ffff]', '', text)

        # Normalize whitespace
        text = " ".join(text.split())
        return text

    async def synthesize_chunk(self, text: str) -> np.ndarray | None:
        """
        Synthesize a single text chunk to audio.
        Routes to Kokoro for English and Piper for Indonesian.
        """
        text = self._sanitize_text(text)
        if not text:
            return None

        # Determine target language
        if self._stt_lang_locked:
            target_lang = self._current_lang
        elif self.lang == "auto":
            target_lang = self._detect_text_language(text)
        else:
            target_lang = "id" if self.lang.lower().startswith("id") else "en"

        loop = asyncio.get_event_loop()

        # ── Indonesian Routing (Piper TTS) ───────────────────────────
        if target_lang == "id" and self._piper_available:
            try:
                log.debug(f"[TTS] Synthesizing Indonesian (Piper): {text!r}")
                audio = await self._piper_client.synthesize_chunk(text)
                if audio is not None:
                    # Resample Piper output (22050 Hz) to target sample rate (24000 Hz)
                    piper_sr = self._piper_client.sample_rate
                    if piper_sr != self.sample_rate:
                        audio = _resample_audio(audio, piper_sr, self.sample_rate)
                    return audio
            except Exception as e:
                log.warning(f"[TTS] Piper synthesis failed, trying Kokoro fallback: {e}")

        # ── English Routing / Primary (Kokoro TTS) ───────────────────
        if self._kokoro_available and self._pipeline is not None:
            def _synth_kokoro() -> np.ndarray | None:
                try:
                    log.debug(f"[TTS] Synthesizing English (Kokoro): {text!r}")
                    generator = self._pipeline(
                        text,
                        voice=self.voice,
                        speed=self.speed,
                        split_pattern=None,
                    )
                    audio_segments = []
                    for result in generator:
                        audio = result[-1]
                        if audio is not None and hasattr(audio, "__len__") and len(audio) > 0:
                            audio_segments.append(audio)

                    if audio_segments:
                        full_audio = np.concatenate(audio_segments)
                        return full_audio.astype(np.float32)
                    return None
                except Exception as e:
                    log.error(f"[TTS] Kokoro synthesis error: {e}")
                    return None

            audio = await loop.run_in_executor(None, _synth_kokoro)
            if audio is not None:
                return audio

        # ── Fallback to Piper if Kokoro fails or unavailable ─────────
        if self._piper_available:
            try:
                log.debug(f"[TTS] Fallback synthesis via Piper: {text!r}")
                audio = await self._piper_client.synthesize_chunk(text)
                if audio is not None:
                    piper_sr = self._piper_client.sample_rate
                    if piper_sr != self.sample_rate:
                        audio = _resample_audio(audio, piper_sr, self.sample_rate)
                    return audio
            except Exception as e:
                log.error(f"[TTS] Fallback Piper synthesis error: {e}")

        return None

    async def process_tts_queue(
        self,
        tts_chunk_queue: asyncio.Queue,
        interrupt_event: asyncio.Event,
    ) -> None:
        """Worker loop: drain text chunks, synthesize, put into audio queue."""
        # Unlock STT language at start of queue processing
        self._stt_lang_locked = False

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
                self._stt_lang_locked = False
                continue

            if interrupt_event.is_set():
                tts_chunk_queue.task_done()
                continue

            try:
                log.debug(f"[TTS] Synthesizing chunk: {chunk!r}")
                audio = await self.synthesize_chunk(chunk)
                if audio is not None and not interrupt_event.is_set():
                    await self.audio_queue.put(audio)
                    log.debug(f"[TTS] → Audio queue ({len(audio)} samples)")
            except Exception as e:
                log.error(f"[TTS] Queue worker error: {e}", exc_info=True)
            finally:
                tts_chunk_queue.task_done()

    async def speak(self, text: str) -> None:
        """Convenience method to synthesize full text directly."""
        from utils.chunk_assembler import split_into_chunks

        chunks = split_into_chunks(text, min_words=2, max_words=25)
        if not chunks:
            chunks = [text]

        for chunk in chunks:
            try:
                audio = await self.synthesize_chunk(chunk)
                if audio is not None:
                    await self.audio_queue.put(audio)
                    await asyncio.sleep(0.05)
            except Exception as e:
                log.warning(f"[TTS] Speak chunk failed: {e}")

        # End-of-stream sentinel
        await self.audio_queue.put(None)
